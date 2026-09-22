"""Persistent per-call context, approvals, Twilio progress and conversation turns."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from app.store import Store, StoreError, now

TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled", "not-placed"}
ACTIVE = {"dialling", "queued", "initiated", "ringing", "in-progress", "unknown"}
RANK = {"dialling": 0, "unknown": 0, "queued": 1, "initiated": 1, "ringing": 2, "in-progress": 3}


class VoiceCallContextService:
    def __init__(self, store: Store):
        self.store = store

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS voice_calls (
                    id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id),
                    to_number TEXT NOT NULL, from_number TEXT NOT NULL, context TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1,
                    ready_to_call INTEGER NOT NULL DEFAULT 0, approved_version INTEGER,
                    approved_at REAL, attempted_at REAL, call_sid TEXT UNIQUE,
                    status_sequence INTEGER NOT NULL DEFAULT -1, last_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voice_turns (
                    call_id TEXT NOT NULL REFERENCES voice_calls(id), turn INTEGER NOT NULL,
                    heard TEXT NOT NULL, reply TEXT NOT NULL, twiml TEXT NOT NULL,
                    end_call INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(call_id,turn)
                );
                CREATE TABLE IF NOT EXISTS voice_suppression (
                    phone TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS voice_lead ON voice_calls(lead_id,created_at);
            """)
            # Never dial again on restart if creation may already have reached Twilio.
            db.execute("UPDATE voice_calls SET status='unknown',last_error=? WHERE status='dialling'",
                       ("Backend restarted during call creation. Check Twilio before retrying.",))

    @staticmethod
    def decode(row):
        item = dict(row)
        item["context"] = json.loads(item["context"])
        item["ready_to_call"] = bool(item["ready_to_call"])
        return item

    def get_call_context(self, call_id: str, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.get_call_context(call_id, connection)
        row = db.execute("SELECT * FROM voice_calls WHERE id=?", (call_id,)).fetchone()
        if row is None:
            raise StoreError(404, "Call plan not found.")
        return self.decode(row)

    def save_call_context(self, lead_id: str, context: dict, to_number: str, from_number: str):
        call_id = uuid4().hex
        with self.store.connection(write=True) as db:
            if db.execute("SELECT 1 FROM voice_suppression WHERE phone=?", (to_number,)).fetchone():
                raise StoreError(409, "This phone number has requested no further calls.")
            db.execute("""INSERT INTO voice_calls(id,lead_id,to_number,from_number,context,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?)""",
                       (call_id, lead_id, to_number, from_number, json.dumps(context), now(), now()))
        return self.get_call_context(call_id)

    def list_calls(self, lead_id: str):
        with self.store.connection() as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM voice_calls WHERE lead_id=? ORDER BY created_at DESC LIMIT 20", (lead_id,))]

    def approve(self, call_id: str, version: int, ready: bool):
        with self.store.connection(write=True) as db:
            row = self.get_call_context(call_id, db)
            if row["version"] != version or row["status"] not in ("draft", "ready"):
                raise StoreError(409, "The call plan changed or was already used. Refresh it.")
            db.execute("""UPDATE voice_calls SET version=version+1,ready_to_call=?,status=?,
                          approved_version=?,approved_at=?,updated_at=? WHERE id=?""",
                       (int(ready), "ready" if ready else "draft", version + 1 if ready else None,
                        time.time() if ready else None, now(), call_id))
        return self.get_call_context(call_id)

    def claim(self, call_id: str, version: int, daily_limit: int, from_number: str):
        with self.store.connection(write=True) as db:
            row = self.get_call_context(call_id, db)
            # Repeated submissions return this attempt; they never dial twice.
            if row["attempted_at"] is not None:
                return row, False
            if (row["version"] != version or row["status"] != "ready" or not row["ready_to_call"]
                    or row["approved_version"] != version):
                raise StoreError(409, "Mark this exact saved call plan Ready to call first.")
            if time.time() - row["approved_at"] > 3600 or row["from_number"] != from_number:
                raise StoreError(409, "Call approval expired or the caller number changed. Prepare and approve a new plan.")
            current_lead = db.execute("SELECT payload FROM leads WHERE id=?", (row["lead_id"],)).fetchone()
            saved_version = row["context"].get("prospect", {}).get("contact_version", 1)
            if not current_lead or json.loads(current_lead[0]).get("contact_version", 1) != saved_version:
                raise StoreError(409, "Contact details changed. Prepare and approve a new call plan.")
            mock = db.execute("SELECT r.mock FROM leads l JOIN runs r ON r.id=l.run_id WHERE l.id=?", (row["lead_id"],)).fetchone()
            if not mock or mock[0]:
                raise StoreError(409, "Sample leads cannot receive real calls.")
            if db.execute("SELECT 1 FROM voice_suppression WHERE phone=?", (row["to_number"],)).fetchone():
                raise StoreError(409, "This number is on the do-not-call list.")
            if db.execute("""SELECT 1 FROM voice_calls WHERE to_number=? AND id<>?
                             AND status IN ('dialling','queued','initiated','ringing','in-progress','unknown')""",
                          (row["to_number"], call_id)).fetchone():
                raise StoreError(409, "A call to this number is active or unresolved. Refresh its status first.")
            midnight = time.time() - (time.time() % 86400)
            count = db.execute("SELECT COUNT(*) FROM voice_calls WHERE attempted_at>=?", (midnight,)).fetchone()[0]
            if count >= daily_limit:
                raise StoreError(429, "Daily voice call limit reached.")
            db.execute("UPDATE voice_calls SET status='dialling',ready_to_call=0,attempted_at=?,updated_at=? WHERE id=?",
                       (time.time(), now(), call_id))
            return self.get_call_context(call_id, db), True

    def bind(self, call_id: str, sid: str, to_number: str, from_number: str):
        with self.store.connection(write=True) as db:
            row = self.get_call_context(call_id, db)
            if (row["attempted_at"] is None or row["to_number"] != to_number or row["from_number"] != from_number
                    or (row["call_sid"] is not None and row["call_sid"] != sid)):
                raise StoreError(403, "Twilio call does not match the approved destination and caller.")
            db.execute("UPDATE voice_calls SET call_sid=? WHERE id=?", (sid, call_id))
        return self.get_call_context(call_id)

    def status(self, call_id: str, status: str, sequence: int | None = None):
        if status not in TERMINAL | set(RANK):
            return self.get_call_context(call_id)
        with self.store.connection(write=True) as db:
            row = self.get_call_context(call_id, db)
            if (row["status"] in TERMINAL or (sequence is not None and sequence <= row["status_sequence"])
                    or (status not in TERMINAL and RANK.get(status, 0) < RANK.get(row["status"], 0))):
                return row
            db.execute("UPDATE voice_calls SET status=?,status_sequence=?,updated_at=? WHERE id=?",
                       (status, sequence if sequence is not None else row["status_sequence"], now(), call_id))
        return self.get_call_context(call_id)

    def creation_error(self, call_id: str, detail: str, uncertain: bool):
        with self.store.connection(write=True) as db:
            # A valid early status callback takes precedence over a lost create response.
            db.execute("UPDATE voice_calls SET status=?,last_error=?,updated_at=? WHERE id=? AND status='dialling' AND call_sid IS NULL",
                       ("unknown" if uncertain else "failed", detail, now(), call_id))
        return self.get_call_context(call_id)

    def resolve_not_placed(self, call_id: str):
        with self.store.connection(write=True) as db:
            row = self.get_call_context(call_id, db)
            if row["status"] != "unknown" or row["call_sid"]:
                raise StoreError(409, "Only an unknown call without a Twilio SID can be resolved this way.")
            db.execute("UPDATE voice_calls SET status='not-placed',last_error=?,updated_at=? WHERE id=?",
                       ("User verified in Twilio that no call was placed.", now(), call_id))
        return self.get_call_context(call_id)

    def transcript(self, call_id: str):
        with self.store.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT turn,heard,reply,end_call,created_at FROM voice_turns WHERE call_id=? ORDER BY turn", (call_id,))]

    def turn_response(self, call_id: str, turn: int):
        with self.store.connection() as db:
            row = db.execute("SELECT twiml FROM voice_turns WHERE call_id=? AND turn=?", (call_id, turn)).fetchone()
            return row[0] if row else None

    def save_turn(self, call_id: str, turn: int, heard: str, reply: str, twiml: str, end_call: bool, opt_out=False):
        with self.store.connection(write=True) as db:
            db.execute("INSERT OR IGNORE INTO voice_turns VALUES(?,?,?,?,?,?,?)",
                       (call_id, turn, heard, reply, twiml, int(end_call), now()))
            if opt_out:
                phone = self.get_call_context(call_id, db)["to_number"]
                db.execute("INSERT OR IGNORE INTO voice_suppression VALUES(?,?,?)", (phone, "Recipient requested no further calls", now()))
        return twiml
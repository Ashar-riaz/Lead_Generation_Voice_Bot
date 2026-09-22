"""SQLite persistence and atomic approval/send reservations.

All checks that reserve a send run in one BEGIN IMMEDIATE transaction. Delivery
happens after commit. A lost provider acknowledgement is never retried automatically.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.mailbox_target import mailbox_key


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoreError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, query TEXT NOT NULL,
                    mock INTEGER NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    lead_id TEXT REFERENCES leads(id), company_name TEXT NOT NULL,
                    to_name TEXT NOT NULL, to_email TEXT NOT NULL, subject TEXT NOT NULL,
                    body TEXT NOT NULL, programme TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', ready_to_send INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1, approved_version INTEGER,
                    approved_at TEXT, sent_at TEXT, last_error TEXT, message_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, email_id TEXT NOT NULL REFERENCES emails(id),
                    recipient TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT, error TEXT
                );
                CREATE INDEX IF NOT EXISTS leads_run ON leads(run_id);
                CREATE INDEX IF NOT EXISTS emails_run ON emails(run_id);
                CREATE INDEX IF NOT EXISTS attempts_recipient ON attempts(recipient, status);
            """)
            # Additive migration: preserve existing leads, drafts and delivery history.
            for table, additions in {
                "emails": {"approved_by": "TEXT", "sent_by": "TEXT", "sender_email": "TEXT", "approved_mailbox": "TEXT"},
                "attempts": {"sender_oid": "TEXT", "sender_email": "TEXT", "provider": "TEXT"},
            }.items():
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, kind in additions.items():
                    if name not in columns:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            # Old password/SMTP approvals have no Microsoft sender identity.
            db.execute("""UPDATE emails SET status='draft',ready_to_send=0,approved_version=NULL,
                          approved_at=NULL,version=version+1 WHERE status='ready' AND approved_by IS NULL""")

    def recover_interrupted(self):
        # Run exactly one API worker. See README before using another deployment model.
        with self.connection(write=True) as db:
            db.execute("UPDATE runs SET status='failed', error=?, updated_at=? WHERE status IN ('queued','running')",
                       ("Server restarted during this search. Start a new search to retry.", now()))
            db.execute("UPDATE emails SET status='unknown', ready_to_send=0, approved_version=NULL, last_error=?, updated_at=? WHERE status='sending'",
                       ("Server restarted during delivery. Check the mail server before resolving this status.", now()))
            db.execute("UPDATE attempts SET status='unknown', error=? WHERE status='reserved'",
                       ("Server restarted during delivery",))

    def create_run(self, request: dict) -> dict:
        run_id, timestamp = uuid4().hex, now()
        with self.connection(write=True) as db:
            active = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if active >= 3:
                raise StoreError(429, "Three searches are already running. Wait for one to finish.")
            db.execute("INSERT INTO runs(id,status,query,mock,request,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (run_id, "queued", request["query"], request["mock"], json.dumps(request), timestamp, timestamp))
        return self.run(run_id)

    @staticmethod
    def decode_run(row) -> dict:
        value = dict(row)
        value["mock"] = bool(value["mock"])
        value["request"] = json.loads(value["request"])
        value["result"] = json.loads(value["result"])
        return value

    @staticmethod
    def decode_email(row) -> dict:
        value = dict(row)
        value["ready_to_send"] = bool(value["ready_to_send"])
        return value

    def run(self, run_id: str, full=False) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise StoreError(404, "Search not found")
            value = self.decode_run(row)
            counts = db.execute("SELECT COUNT(*), COALESCE(SUM(status='ready'),0), COALESCE(SUM(status='sent'),0) FROM emails WHERE run_id=?", (run_id,)).fetchone()
            value.update(email_count=counts[0], ready_count=counts[1], sent_count=counts[2])
            value["lead_count"] = db.execute("SELECT COUNT(*) FROM leads WHERE run_id=?", (run_id,)).fetchone()[0]
            if full:
                value["leads"] = [{"id": r["id"], "run_id": run_id, **json.loads(r["payload"])} for r in db.execute("SELECT * FROM leads WHERE run_id=? ORDER BY rowid", (run_id,))]
                value["emails"] = [self.decode_email(r) for r in db.execute("SELECT * FROM emails WHERE run_id=? ORDER BY rowid", (run_id,))]
            return value

    def list_runs(self, limit=50, offset=0, live_only=False):
        where = " WHERE mock=0" if live_only else ""
        with self.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))]
            total = db.execute("SELECT COUNT(*) FROM runs" + where).fetchone()[0]
        return {"items": [self.run(i) for i in ids], "total": total}

    def progress(self, run_id: str, state: dict, status="running", error=None):
        summary = {k: state[k] for k in ("plan", "progress", "errors") if k in state}
        with self.connection(write=True) as db:
            db.execute("UPDATE runs SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                       (status, json.dumps(summary), error, now(), run_id))

    def finish_run(self, run_id: str, state: dict):
        with self.connection(write=True) as db:
            by_company, by_name = {}, {}
            for lead in state.get("leads", []):
                lead_id = uuid4().hex
                by_company[lead["company_id"]] = lead_id
                by_name[lead["company_name"]] = lead_id
                db.execute("INSERT INTO leads VALUES(?,?,?)", (lead_id, run_id, json.dumps(lead)))
            for draft in state.get("drafts", []):
                lead_id = by_company.get(draft.get("company_id")) or by_name.get(draft["company_name"])
                db.execute("""INSERT INTO emails(id,run_id,lead_id,company_name,to_name,to_email,subject,body,programme,updated_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""", (uuid4().hex, run_id, lead_id, draft["company_name"],
                              draft["to_name"], draft["to_email"], draft["subject"], draft["body"], draft.get("programme", ""), now()))
            summary = {k: state.get(k, [] if k != "plan" else {}) for k in ("plan", "progress", "errors")}
            db.execute("UPDATE runs SET status='completed', result=?, updated_at=? WHERE id=?", (json.dumps(summary), now(), run_id))

    def list_leads(self, run_id=None, tier=None, q="", limit=50, offset=0):
        conditions, args = [], []
        if run_id:
            conditions.append("run_id=?"); args.append(run_id)
        if tier:
            conditions.append("json_extract(payload,'$.tier')=?"); args.append(tier)
        if q:
            conditions.append("LOWER(json_extract(payload,'$.company_name')) LIKE ?"); args.append("%" + q.lower() + "%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            total = db.execute("SELECT COUNT(*) FROM leads" + where, args).fetchone()[0]
            rows = db.execute("SELECT * FROM leads" + where + " ORDER BY rowid DESC LIMIT ? OFFSET ?", args + [limit, offset])
            return {"items": [{"id": r["id"], "run_id": r["run_id"], **json.loads(r["payload"])} for r in rows], "total": total}

    def lead(self, lead_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                raise StoreError(404, "Lead not found")
            return {"id": row["id"], "run_id": row["run_id"], **json.loads(row["payload"])}

    def email(self, email_id, db=None):
        if db is None:
            with self.connection() as conn:
                return self.email(email_id, conn)
        row = db.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            raise StoreError(404, "Email not found")
        return self.decode_email(row)

    def list_emails(self, run_id=None, status=None, limit=50, offset=0):
        conditions, args = [], []
        if run_id:
            conditions.append("run_id=?"); args.append(run_id)
        if status:
            conditions.append("status=?"); args.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            total = db.execute("SELECT COUNT(*) FROM emails" + where, args).fetchone()[0]
            rows = db.execute("SELECT * FROM emails" + where + " ORDER BY rowid DESC LIMIT ? OFFSET ?", args + [limit, offset])
            return {"items": [self.decode_email(r) for r in rows], "total": total}

    @staticmethod
    def editable(row, expected_version):
        if row["version"] != expected_version:
            raise StoreError(409, "This draft changed in another window. Refresh and review it again.")
        if row["status"] in ("sent", "sending", "unknown"):
            raise StoreError(409, "Sent, sending, or unresolved emails cannot be edited or approved.")

    def edit_email(self, email_id: str, data: dict):
        with self.connection(write=True) as db:
            row = self.email(email_id, db)
            self.editable(row, data["expected_version"])
            db.execute("""UPDATE emails SET to_email=?,to_name=?,subject=?,body=?,version=version+1,
                          status='draft',ready_to_send=0,approved_version=NULL,approved_by=NULL,approved_at=NULL,last_error=NULL,updated_at=? WHERE id=?""",
                       (str(data["to_email"]), data["to_name"], data["subject"], data["body"], now(), email_id))
        return self.email(email_id)

    def approve(self, email_id: str, version: int, ready: bool, actor_id: str | None = None, mailbox: str | None = None):
        if ready and not actor_id:
            raise StoreError(401, "A connected Microsoft mailbox is required to approve email sending")
        with self.connection(write=True) as db:
            row = self.email(email_id, db)
            self.editable(row, version)
            new_version = version + 1
            db.execute("""UPDATE emails SET ready_to_send=?,status=?,version=?,approved_version=?,approved_at=?,approved_by=?,approved_mailbox=?,last_error=NULL,updated_at=? WHERE id=?""",
                       (int(ready), "ready" if ready else "draft", new_version, new_version if ready else None,
                        now() if ready else None, actor_id if ready else None, (mailbox or actor_id) if ready else None, now(), email_id))
        return self.email(email_id)

    def claim_send(self, email_id: str, run_id: str, version: int, daily_limit: int, suppressed: set, legacy_sent: set, legacy_today: set, actor: dict | None = None):
        with self.connection(write=True) as db:
            if not actor or not db.execute("SELECT 1 FROM ms_sessions WHERE token_hash=? AND tenant_id=? AND object_id=? AND expires_at>?",
                                            (actor.get("session_hash"), actor.get("tenant_id"), actor.get("object_id"), time.time())).fetchone():
                raise StoreError(401, "Your Microsoft mailbox connection expired or was disconnected")
            row = self.email(email_id, db)
            if row["run_id"] != run_id:
                raise StoreError(409, "Email does not belong to the selected search")
            if row["status"] == "sent":
                raise StoreError(409, "Already sent; no duplicate was sent")
            if row["version"] != version or row["status"] != "ready" or not row["ready_to_send"] or row["approved_version"] != version:
                raise StoreError(409, "This exact draft must be marked Ready to send first")
            if row["approved_by"] != actor["object_id"]:
                raise StoreError(409, "Review and approve this draft with your Microsoft account before sending from your mailbox")
            if row["approved_mailbox"] != mailbox_key(actor):
                raise StoreError(409, "The sending mailbox changed. Review and approve this draft again before sending.")
            mock = db.execute("SELECT mock FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            if mock:
                raise StoreError(409, "Sample searches cannot send real emails. Run a live search first.")
            recipient = row["to_email"].strip().lower()
            if recipient in suppressed:
                raise StoreError(409, "Recipient is on the suppression list")
            duplicate = db.execute("SELECT 1 FROM attempts WHERE recipient=? AND status IN ('reserved','sent','unknown')", (recipient,)).fetchone()
            if duplicate or recipient in legacy_sent:
                raise StoreError(409, "This recipient already has a sent, pending, or unresolved email")
            today = now()[:10]
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE substr(created_at,1,10)=? AND status IN ('reserved','sent','unknown')", (today,)).fetchone()[0]
            if count + len(legacy_today) >= daily_limit:
                raise StoreError(429, "Daily send limit reached")
            attempt_id = uuid4().hex
            # A correlation header, not Graph's or Exchange's internet Message-ID.
            message_id = f"wtd-{attempt_id}"
            db.execute("INSERT INTO attempts(id,email_id,recipient,status,message_id,created_at,sender_oid,sender_email,provider) VALUES(?,?,?,'reserved',?,?,?,?,?)",
                       (attempt_id, email_id, recipient, message_id, now(), actor["object_id"], actor["email"], "microsoft_graph"))
            db.execute("UPDATE emails SET status='sending',ready_to_send=0,message_id=?,sent_by=?,sender_email=?,updated_at=? WHERE id=?",
                       (message_id, actor["object_id"], actor["email"], now(), email_id))
            return {**row, "attempt_id": attempt_id, "message_id": message_id}

    def finish_send(self, claim: dict, status: str, error=None):
        with self.connection(write=True) as db:
            db.execute("UPDATE attempts SET status=?,completed_at=?,error=? WHERE id=?", (status, now(), error, claim["attempt_id"]))
            db.execute("""UPDATE emails SET status=?,ready_to_send=0,approved_version=NULL,
                          sent_at=?,last_error=?,updated_at=? WHERE id=?""",
                       (status, now() if status == "sent" else None, error, now(), claim["id"]))
        return self.email(claim["id"])

    def resolve(self, email_id: str, version: int, delivered: bool):
        with self.connection(write=True) as db:
            row = self.email(email_id, db)
            if row["version"] != version or row["status"] != "unknown":
                raise StoreError(409, "Only the current unresolved delivery can be resolved")
            status = "sent" if delivered else "failed"
            db.execute("UPDATE attempts SET status=?,completed_at=?,error=? WHERE email_id=? AND status='unknown'",
                       (status, now(), "Delivery outcome verified manually", email_id))
            db.execute("""UPDATE emails SET status=?,version=version+1,sent_at=?,ready_to_send=0,
                          approved_version=NULL,approved_at=NULL,last_error=NULL,updated_at=? WHERE id=?""",
                       (status, now() if delivered else None, now(), email_id))
        return self.email(email_id)

    def attempts(self, email_id: str):
        self.email(email_id)
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM attempts WHERE email_id=? ORDER BY created_at DESC", (email_id,))]
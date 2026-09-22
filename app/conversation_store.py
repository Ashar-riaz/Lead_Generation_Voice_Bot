"""Mailbox-scoped conversations and versioned, manually approved reply drafts."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from app.store import StoreError, now


class ConversationStore:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mail_threads (
                    email_id TEXT PRIMARY KEY REFERENCES emails(id), tenant_id TEXT NOT NULL,
                    mailbox_id TEXT NOT NULL, conversation_id TEXT, from_address TEXT,
                    scan_cursor TEXT, message_cursor TEXT, sync_state TEXT NOT NULL DEFAULT 'new',
                    synced_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mail_messages (
                    id TEXT PRIMARY KEY, email_id TEXT NOT NULL REFERENCES mail_threads(email_id),
                    provider_id TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(email_id,provider_id)
                );
                CREATE TABLE IF NOT EXISTS mail_replies (
                    id TEXT PRIMARY KEY, email_id TEXT NOT NULL REFERENCES mail_threads(email_id),
                    message_id TEXT NOT NULL REFERENCES mail_messages(id), body TEXT NOT NULL DEFAULT '',
                    recipients TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
                    version INTEGER NOT NULL DEFAULT 1, ready_to_send INTEGER NOT NULL DEFAULT 0,
                    approved_version INTEGER, attempt_id TEXT REFERENCES attempts(id), last_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(email_id,message_id)
                );
            """)
            db.execute("""UPDATE mail_replies SET status='unknown',ready_to_send=0,approved_version=NULL,
                          last_error='Backend restarted while sending. Check Outlook Sent Items before resolving.'
                          WHERE status='sending'""")

    def ensure(self, email_id, actor, db=None):
        if db is None:
            with self.store.connection(write=True) as connection:
                return self.ensure(email_id, actor, connection)
        email = self.store.email(email_id, db)
        if email["sent_by"] != actor["object_id"]:
            raise StoreError(403, "Connect the Microsoft mailbox that sent this email to view or reply to its conversation.")
        if email["status"] != "sent" or not email["message_id"]:
            raise StoreError(409, "Conversations are available after the original email is confirmed sent.")
        if db.execute("SELECT mock FROM runs WHERE id=?", (email["run_id"],)).fetchone()[0]:
            raise StoreError(409, "Sample leads cannot sync or send real email.")
        db.execute("INSERT OR IGNORE INTO mail_threads(email_id,tenant_id,mailbox_id) VALUES(?,?,?)",
                   (email_id, actor["tenant_id"], actor["object_id"]))
        thread = dict(db.execute("SELECT * FROM mail_threads WHERE email_id=?", (email_id,)).fetchone())
        if thread["tenant_id"] != actor["tenant_id"] or thread["mailbox_id"] != actor["object_id"]:
            raise StoreError(403, "This conversation belongs to another mailbox.")
        return email, thread

    def discovery(self, email_id, original, cursor):
        with self.store.connection(write=True) as db:
            if original:
                db.execute("""UPDATE mail_threads SET conversation_id=?,from_address=?,scan_cursor=NULL,
                              sync_state='syncing' WHERE email_id=?""",
                           (original["conversationId"], original.get("from", {}).get("emailAddress", {}).get("address", ""), email_id))
            else:
                db.execute("UPDATE mail_threads SET scan_cursor=?,sync_state=? WHERE email_id=?",
                           (cursor, "searching" if cursor else "not_found", email_id))

    def save_messages(self, email_id, messages, cursor):
        with self.store.connection(write=True) as db:
            for message in messages:
                db.execute("""INSERT INTO mail_messages(id,email_id,provider_id,payload) VALUES(?,?,?,?)
                              ON CONFLICT(email_id,provider_id) DO UPDATE SET payload=excluded.payload""",
                           (uuid4().hex, email_id, message["provider_id"], json.dumps(message)))
            db.execute("UPDATE mail_threads SET message_cursor=?,sync_state=?,synced_at=? WHERE email_id=?",
                       (cursor, "syncing" if cursor else "synced", now(), email_id))

    def message(self, email_id, message_id, db):
        row = db.execute("SELECT * FROM mail_messages WHERE id=? AND email_id=?", (message_id, email_id)).fetchone()
        if not row:
            raise StoreError(404, "Conversation message not found.")
        return {"id": row["id"], **json.loads(row["payload"])}

    @staticmethod
    def decode_reply(row):
        result = dict(row)
        result["recipients"] = json.loads(result["recipients"])
        result["ready_to_send"] = bool(result["ready_to_send"])
        return result

    def reply(self, email_id, reply_id, actor, db=None):
        if db is None:
            with self.store.connection(write=True) as connection:
                return self.reply(email_id, reply_id, actor, connection)
        self.ensure(email_id, actor, db)
        row = db.execute("SELECT * FROM mail_replies WHERE id=? AND email_id=?", (reply_id, email_id)).fetchone()
        if not row:
            raise StoreError(404, "Reply draft not found.")
        return self.decode_reply(row)

    def view(self, email_id, actor):
        with self.store.connection(write=True) as db:
            email, thread = self.ensure(email_id, actor, db)
            messages = [{"id": r["id"], **json.loads(r["payload"])} for r in db.execute(
                "SELECT * FROM mail_messages WHERE email_id=?", (email_id,))]
            for message in messages:
                message.pop("provider_id", None)
                message.pop("internet_message_id", None)
            replies = [self.decode_reply(r) for r in db.execute(
                "SELECT * FROM mail_replies WHERE email_id=? ORDER BY created_at DESC", (email_id,))]
        return {"email_id": email_id, "mailbox": actor["email"], "state": thread["sync_state"],
                "synced_at": thread["synced_at"], "more_available": bool(thread["scan_cursor"] or thread["message_cursor"]),
                "messages": sorted(messages, key=lambda m: (m["timestamp"], m["id"])), "replies": replies}

    def draft(self, email_id, message_id, actor):
        with self.store.connection(write=True) as db:
            self.ensure(email_id, actor, db)
            message = self.message(email_id, message_id, db)
            if not message["can_reply"]:
                raise StoreError(409, "Choose a received message that accepts replies.")
            existing = db.execute("SELECT * FROM mail_replies WHERE email_id=? AND message_id=?", (email_id, message_id)).fetchone()
            if existing:
                return self.decode_reply(existing)
            reply_id = uuid4().hex
            db.execute("INSERT INTO mail_replies(id,email_id,message_id,recipients,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (reply_id, email_id, message_id, json.dumps(message["reply_to"]), now(), now()))
            return self.reply(email_id, reply_id, actor, db)

    @staticmethod
    def editable(row, version):
        if row["version"] != version:
            raise StoreError(409, "This reply changed in another window. Reload and review it again.")
        if row["status"] in ("sent", "sending", "unknown"):
            raise StoreError(409, "A sent, sending or unresolved reply cannot be edited or approved.")

    def edit(self, email_id, reply_id, actor, version, body):
        with self.store.connection(write=True) as db:
            row = self.reply(email_id, reply_id, actor, db)
            self.editable(row, version)
            message = self.message(email_id, row["message_id"], db)
            if not message["can_reply"]:
                raise StoreError(409, "This received message no longer accepts replies.")
            db.execute("""UPDATE mail_replies SET body=?,recipients=?,version=version+1,status='draft',
                          ready_to_send=0,approved_version=NULL,last_error=NULL,updated_at=? WHERE id=?""",
                       (body, json.dumps(message["reply_to"]), now(), reply_id))
            return self.reply(email_id, reply_id, actor, db)

    def approve(self, email_id, reply_id, actor, version, ready):
        with self.store.connection(write=True) as db:
            row = self.reply(email_id, reply_id, actor, db)
            self.editable(row, version)
            if ready and not row["body"].strip():
                raise StoreError(422, "Write and save your response before approving it.")
            db.execute("""UPDATE mail_replies SET version=version+1,status=?,ready_to_send=?,
                          approved_version=?,last_error=NULL,updated_at=? WHERE id=?""",
                       ("ready" if ready else "draft", int(ready), version + 1 if ready else None, now(), reply_id))
            return self.reply(email_id, reply_id, actor, db)

    def claim(self, email_id, reply_id, actor, version, daily_limit, suppressed, legacy_today):
        with self.store.connection(write=True) as db:
            row = self.reply(email_id, reply_id, actor, db)
            if row["status"] in ("sent", "sending", "unknown"):
                return row, False
            if not db.execute("SELECT 1 FROM ms_sessions WHERE token_hash=? AND tenant_id=? AND object_id=? AND expires_at>?",
                              (actor["session_hash"], actor["tenant_id"], actor["object_id"], time.time())).fetchone():
                raise StoreError(401, "Your mailbox connection expired. Reconnect before sending.")
            if (row["status"] != "ready" or not row["ready_to_send"] or row["version"] != version
                    or row["approved_version"] != version or not row["body"].strip()):
                raise StoreError(409, "Save and mark this exact reply Ready to send first.")
            if any(r["address"].lower() in suppressed for r in row["recipients"]):
                raise StoreError(409, "A reply recipient is on the email suppression list.")
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE substr(created_at,1,10)=? AND status IN ('reserved','sent','unknown')",
                               (now()[:10],)).fetchone()[0]
            if count + len(legacy_today) >= daily_limit:
                raise StoreError(429, "Daily email send limit reached.")
            attempt_id = uuid4().hex
            db.execute("""INSERT INTO attempts(id,email_id,recipient,status,message_id,created_at,sender_oid,sender_email,provider)
                          VALUES(?,?,?,'reserved',?,?,?,?,?)""",
                       (attempt_id, email_id, ";".join(r["address"].lower() for r in row["recipients"]),
                        "wtd-reply-" + attempt_id, now(), actor["object_id"], actor["email"], "microsoft_graph_reply"))
            db.execute("UPDATE mail_replies SET status='sending',ready_to_send=0,attempt_id=?,updated_at=? WHERE id=?",
                       (attempt_id, now(), reply_id))
            return self.reply(email_id, reply_id, actor, db), True

    def finish(self, reply_id, attempt_id, status, error=None):
        with self.store.connection(write=True) as db:
            db.execute("UPDATE attempts SET status=?,completed_at=?,error=? WHERE id=?", (status, now(), error, attempt_id))
            db.execute("UPDATE mail_replies SET status=?,ready_to_send=0,approved_version=NULL,last_error=?,updated_at=? WHERE id=? AND attempt_id=?",
                       (status, error, now(), reply_id, attempt_id))

    def resolve(self, email_id, reply_id, actor, version, delivered):
        with self.store.connection(write=True) as db:
            row = self.reply(email_id, reply_id, actor, db)
            if row["status"] != "unknown" or row["version"] != version:
                raise StoreError(409, "Only the current unresolved reply can be resolved. Reload first.")
            status = "sent" if delivered else "failed"
            db.execute("UPDATE attempts SET status=?,completed_at=?,error=? WHERE id=?",
                       (status, now(), "Delivery outcome verified manually", row["attempt_id"]))
            db.execute("UPDATE mail_replies SET status=?,version=version+1,ready_to_send=0,approved_version=NULL,last_error=NULL,updated_at=? WHERE id=?",
                       (status, now(), reply_id))
            return self.reply(email_id, reply_id, actor, db)

"""Mailbox identities, revocable connection cookies and encrypted token caches."""
import hashlib
import secrets
import time

from app.store import Store, StoreError, now


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    def __init__(self, store: Store):
        self.store = store

    def initialize(self):
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ms_users (
                    tenant_id TEXT NOT NULL, object_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '', access TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS ms_sessions (
                    token_hash TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, object_id TEXT NOT NULL,
                    expires_at REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ms_caches (
                    tenant_id TEXT NOT NULL, object_id TEXT NOT NULL, encrypted_cache TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS ms_flows (
                    handle_hash TEXT PRIMARY KEY, encrypted_flow TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_audit (
                    id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    action TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    def save_flow(self, encrypted: str) -> str:
        handle = secrets.token_urlsafe(32)
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM ms_flows WHERE expires_at<=?", (time.time(),))
            if db.execute("SELECT COUNT(*) FROM ms_flows").fetchone()[0] >= 100:
                raise StoreError(429, "Too many mailbox connection attempts. Try again in a few minutes.")
            db.execute("INSERT INTO ms_flows VALUES(?,?,?)", (digest(handle), encrypted, time.time() + 600))
        return handle

    def consume_flow(self, handle: str) -> str:
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT * FROM ms_flows WHERE handle_hash=?", (digest(handle),)).fetchone()
            db.execute("DELETE FROM ms_flows WHERE handle_hash=?", (digest(handle),))
        if not row or row["expires_at"] <= time.time():
            raise StoreError(400, "Mailbox connection expired or was already used. Connect again.")
        return row["encrypted_flow"]

    def record_identity(self, tenant: str, oid: str, name: str, email: str) -> dict:
        with self.store.connection(write=True) as db:
            db.execute("""INSERT INTO ms_users(tenant_id,object_id,name,email,created_at,updated_at) VALUES(?,?,?,?,?,?)
                          ON CONFLICT(tenant_id,object_id) DO UPDATE SET name=excluded.name,email=excluded.email,updated_at=excluded.updated_at""",
                       (tenant, oid, name[:200], email[:320], now(), now()))
            return dict(db.execute("SELECT * FROM ms_users WHERE tenant_id=? AND object_id=?", (tenant, oid)).fetchone())

    def user(self, tenant: str, oid: str) -> dict | None:
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM ms_users WHERE tenant_id=? AND object_id=?", (tenant, oid)).fetchone()
            return dict(row) if row else None

    def new_session(self, tenant: str, oid: str, encrypted_cache: str) -> str:
        raw = secrets.token_urlsafe(32)
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM ms_sessions WHERE expires_at<=?", (time.time(),))
            db.execute("INSERT INTO ms_sessions VALUES(?,?,?,?,?)", (digest(raw), tenant, oid, time.time() + 8 * 3600, now()))
            self.save_cache(tenant, oid, encrypted_cache, db)
        return raw

    def session(self, token: str) -> dict | None:
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM ms_sessions WHERE token_hash=? AND expires_at>?", (digest(token), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token: str):
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT * FROM ms_sessions WHERE token_hash=?", (digest(token),)).fetchone()
            db.execute("DELETE FROM ms_sessions WHERE token_hash=?", (digest(token),))
            if row and not db.execute("SELECT 1 FROM ms_sessions WHERE tenant_id=? AND object_id=? AND expires_at>?",
                                      (row["tenant_id"], row["object_id"], time.time())).fetchone():
                db.execute("DELETE FROM ms_caches WHERE tenant_id=? AND object_id=?", (row["tenant_id"], row["object_id"]))
                db.execute("""UPDATE emails SET ready_to_send=0,approved_version=NULL,approved_by=NULL,
                              approved_at=NULL,status='draft',version=version+1,updated_at=?
                              WHERE approved_by=? AND status='ready'""", (now(), row["object_id"]))

    def cache(self, tenant: str, oid: str) -> str | None:
        with self.store.connection() as db:
            row = db.execute("SELECT encrypted_cache FROM ms_caches WHERE tenant_id=? AND object_id=?", (tenant, oid)).fetchone()
            return row[0] if row else None

    def save_cache(self, tenant, oid, encrypted, db=None):
        if db is None:
            with self.store.connection(write=True) as connection:
                return self.save_cache(tenant, oid, encrypted, connection)
        db.execute("""INSERT INTO ms_caches VALUES(?,?,?) ON CONFLICT(tenant_id,object_id)
                      DO UPDATE SET encrypted_cache=excluded.encrypted_cache""", (tenant, oid, encrypted))

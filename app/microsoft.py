"""Optional Microsoft mailbox connection with PKCE and validated ID tokens."""
from __future__ import annotations

import json
import re
from threading import RLock
from urllib.parse import urlparse
from uuid import UUID

import jwt
import msal
import requests
from cryptography.fernet import Fernet, InvalidToken

from app.auth_store import AuthStore
from app.store import StoreError
from config.settings import Settings

SCOPES = ["User.Read", "Mail.Send", "Mail.Read"]  # MSAL adds openid, profile, offline_access.
SESSION_SECONDS = 8 * 3600


class MicrosoftAuth:
    def __init__(self, settings: Settings, store: AuthStore):
        self.settings, self.store = settings, store
        self._lock = RLock()  # One backend worker; refreshes and disconnects are serialized.
        self._jwks = None

    def require_config(self):
        s = self.settings
        if not s.has_microsoft:
            raise StoreError(503, "Microsoft email is not configured. Follow docs/MICROSOFT_SETUP.md to connect a mailbox. Research and draft editing remain available.")
        try:
            UUID(s.microsoft_tenant_id)
            UUID(s.microsoft_client_id)
            Fernet(s.token_encryption_key.encode())
            uri = urlparse(s.microsoft_redirect_uri)
            if not uri.hostname or (uri.scheme != "https" and not (uri.scheme == "http" and uri.hostname in ("localhost", "127.0.0.1"))) or uri.query or uri.fragment or uri.username:
                raise ValueError
        except (ValueError, TypeError):
            raise StoreError(503, "Microsoft email configuration is invalid. Check tenant/client IDs, redirect URI and TOKEN_ENCRYPTION_KEY.") from None

    def encrypt(self, value: str) -> str:
        return Fernet(self.settings.token_encryption_key.encode()).encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return Fernet(self.settings.token_encryption_key.encode()).decrypt(value.encode()).decode()
        except (ValueError, InvalidToken):
            raise StoreError(401, "Reconnect your Microsoft mailbox in Connections to renew mail access.") from None

    def client(self, cache: msal.SerializableTokenCache):
        s = self.settings
        return msal.ConfidentialClientApplication(s.microsoft_client_id, client_credential=s.microsoft_client_secret,
                   authority=f"https://login.microsoftonline.com/{s.microsoft_tenant_id}", token_cache=cache,
                   timeout=20, instance_discovery=False)

    def start(self) -> dict:
        self.require_config()
        try:
            flow = self.client(msal.SerializableTokenCache()).initiate_auth_code_flow(
                scopes=SCOPES, redirect_uri=self.settings.microsoft_redirect_uri,
                response_mode="query", prompt="select_account")
            if "auth_uri" not in flow or "state" not in flow:
                raise ValueError
        except (ValueError, requests.RequestException):
            raise StoreError(502, "Could not connect Microsoft email. Check the app registration and try again.") from None
        handle = self.store.save_flow(self.encrypt(json.dumps(flow)))
        return {"authorization_url": flow["auth_uri"], "flow_handle": handle}

    def validate_id_token(self, raw: str) -> dict:
        """Validate the signature as well as issuer, audience and expiry. MSAL checks nonce/state."""
        s = self.settings
        if self._jwks is None:
            self._jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{s.microsoft_tenant_id}/discovery/v2.0/keys", timeout=15)
        try:
            key = self._jwks.get_signing_key_from_jwt(raw).key
            claims = jwt.decode(raw, key, algorithms=["RS256"], audience=s.microsoft_client_id,
                                issuer=f"https://login.microsoftonline.com/{s.microsoft_tenant_id}/v2.0", leeway=30,
                                options={"require": ["exp", "iat", "nbf", "iss", "aud", "oid", "tid", "nonce"]})
            if not isinstance(claims["tid"], str) or claims["tid"].lower() != s.microsoft_tenant_id:
                raise ValueError
            claims["oid"] = str(UUID(claims["oid"]))
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise StoreError(401, "Microsoft mailbox identity could not be verified. Reconnect using your organisation account.") from None

    def complete(self, handle: str, response: dict) -> dict:
        self.require_config()
        flow = json.loads(self.decrypt(self.store.consume_flow(handle)))
        cache = msal.SerializableTokenCache()
        try:
            result = self.client(cache).acquire_token_by_auth_code_flow(flow, response)
        except (ValueError, RuntimeError, requests.RequestException):
            raise StoreError(400, "Microsoft mailbox connection could not be verified. Connect again.") from None
        if not result.get("id_token") or not result.get("access_token"):
            raise StoreError(401, "Microsoft mailbox connection was not completed. Check consent and try again.")
        claims = self.validate_id_token(result["id_token"])
        s, oid = self.settings, claims["oid"]
        with self._lock:
            user = self.store.record_identity(s.microsoft_tenant_id, oid, claims.get("name", "Microsoft user"),
                                              claims.get("preferred_username") or claims.get("email") or "")
            # This connection authorizes only this mailbox, never workspace access.
            # The legacy workspace allowlist is no longer used. Tenant consent still applies.
            # Resolve the mailbox label rather than assuming a sign-in alias is the mail address.
            try:
                profile_response = requests.get("https://graph.microsoft.com/v1.0/me",
                    params={"$select": "id,displayName,mail,userPrincipalName"},
                    headers={"Authorization": f"Bearer {result['access_token']}"}, timeout=(10, 20), allow_redirects=False)
                profile_response.raise_for_status()
                profile = profile_response.json()
                if profile.get("id", "").lower() != oid:
                    raise ValueError
                mailbox = profile.get("mail") or profile.get("userPrincipalName") or user["email"]
                user = self.store.record_identity(s.microsoft_tenant_id, oid, profile.get("displayName") or user["name"], mailbox)
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                raise StoreError(502, "Could not verify your Microsoft mailbox profile. Check User.Read permission and reconnect.") from None
            token = self.store.new_session(s.microsoft_tenant_id, oid, self.encrypt(cache.serialize()))
            return {"session_token": token, "max_age": SESSION_SECONDS, "mailbox": self.public_user(user)}

    def logout(self, token: str):
        with self._lock:
            self.store.logout(token)

    def public_user(self, user: dict) -> dict:
        return {key: user[key] for key in ("object_id", "tenant_id", "name", "email")}

    def authenticate(self, token: str | None) -> dict:
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise StoreError(401, "Connect a Microsoft mailbox in Connections before approving or sending email.")
        self.require_config()
        session = self.store.session(token)
        if not session or session["tenant_id"] != self.settings.microsoft_tenant_id:
            raise StoreError(401, "Your mailbox connection expired or was disconnected. Reconnect in Connections.")
        user = self.store.user(session["tenant_id"], session["object_id"])
        if not user:
            raise StoreError(401, "Microsoft mailbox connection is missing. Reconnect in Connections.")
        return {**self.public_user(user), "session_hash": session["token_hash"]}

    def access_token(self, actor: dict) -> str:
        self.require_config()
        tenant, oid = actor["tenant_id"], actor["object_id"]
        with self._lock:
            # Recheck the mailbox connection before every send, including batches.
            with self.store.store.connection() as db:
                import time
                active = db.execute("SELECT 1 FROM ms_sessions WHERE token_hash=? AND tenant_id=? AND object_id=? AND expires_at>?",
                                    (actor["session_hash"], tenant, oid, time.time())).fetchone()
            user = self.store.user(tenant, oid)
            if tenant != self.settings.microsoft_tenant_id or not active or not user:
                raise StoreError(401, "Microsoft mailbox connection expired or was disconnected. Reconnect in Connections.")
            encrypted = self.store.cache(tenant, oid)
            if not encrypted:
                raise StoreError(401, "Microsoft mailbox connection is missing. Reconnect in Connections.")
            cache = msal.SerializableTokenCache()
            cache.deserialize(self.decrypt(encrypted))
            try:
                client = self.client(cache)
                accounts = [a for a in client.get_accounts() if a.get("local_account_id", "").lower() == oid and a.get("realm", "").lower() == tenant]
                result = client.acquire_token_silent(SCOPES, account=accounts[0]) if len(accounts) == 1 else None
            except (ValueError, requests.RequestException):
                raise StoreError(502, "Microsoft mailbox connection failed. Try again or reconnect in Connections.") from None
            if cache.has_state_changed:
                self.store.save_cache(tenant, oid, self.encrypt(cache.serialize()))
            if not result or not result.get("access_token"):
                raise StoreError(401, "Reconnect your Microsoft mailbox to renew mail permission.")
            return result["access_token"]

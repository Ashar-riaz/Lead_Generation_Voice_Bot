"""OAuth client credentials; renew access tokens without daily configuration edits.

https://docs.zoominfo.com/docs/client-credentials-flow
This renews access tokens, not a secret revoked/expired by the account admin.
"""
from __future__ import annotations

import math
import time
from functools import lru_cache
from threading import Lock

import requests


class ZoomInfoAuthError(RuntimeError):
    """Safe to show in the dashboard; never includes a provider response body."""


class TokenManager:
    def __init__(self, client_id: str, client_secret: str, token_url: str, timeout: int = 30, scope: str = ""):
        if not client_id or not client_secret:
            raise ZoomInfoAuthError("Set ZOOMINFO_CLIENT_ID and ZOOMINFO_CLIENT_SECRET from a Standard App using Client Credentials.")
        self.client_id, self.client_secret = client_id, client_secret
        self.token_url, self.timeout, self.scope = token_url, timeout, scope
        self._token: str | None = None
        self._expires_at = self._renew_at = 0.0
        self._lock = Lock()

    def get_token(self) -> str:
        # Serialize refreshes: concurrent searches/lookups reuse one fresh token.
        with self._lock:
            if self._token and time.monotonic() < self._renew_at:
                return self._token
            started = time.monotonic()
            body = {"grant_type": "client_credentials"}
            if self.scope:
                body["scope"] = self.scope
            try:
                response = requests.post(self.token_url, auth=(self.client_id, self.client_secret),
                                         data=body, headers={"Accept": "application/json"}, timeout=self.timeout)
            except requests.RequestException:
                raise ZoomInfoAuthError("Could not reach ZoomInfo authentication. Check connectivity and try again.") from None
            if response.status_code in (400, 401, 403):
                raise ZoomInfoAuthError(
                    "ZoomInfo rejected the app credentials or scopes. Use a Standard App with Client Credentials and an integration user. "
                    "Check its client ID, client secret and permissions, then restart the backend. "
                    "A temporary Test API Access token is not a client secret."
                )
            if response.status_code != 200:
                raise ZoomInfoAuthError(f"ZoomInfo authentication is unavailable (HTTP {response.status_code}). Try again later.")
            try:
                data = response.json()
                token = data["access_token"]
                lifetime = float(data["expires_in"])
                if (not isinstance(token, str) or not token.strip() or
                        isinstance(data["expires_in"], bool) or not math.isfinite(lifetime) or lifetime <= 0 or
                        data.get("token_type", "Bearer").lower() != "bearer"):
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ZoomInfoAuthError("ZoomInfo returned an invalid token response; expected access_token and a positive expires_in.") from None
            self._expires_at = started + lifetime
            if self._expires_at <= time.monotonic():
                raise ZoomInfoAuthError("The ZoomInfo token expired during authentication. Try again.")
            # Honor the provider lifetime, including short tokens; never assume 24h.
            self._renew_at = self._expires_at - min(60.0, lifetime * 0.1)
            self._token = token
            return token

    def invalidate(self, rejected_token: str | None = None) -> None:
        with self._lock:
            # A delayed 401 must not invalidate a newer token from another request.
            if rejected_token is None or self._token == rejected_token:
                self._token = None
                self._expires_at = self._renew_at = 0.0

    def status(self) -> dict:
        with self._lock:
            return {"automatic_renewal": True, "expires_in_seconds": max(0, int(self._expires_at - time.monotonic()))}


@lru_cache(maxsize=8)
def _manager(client_id: str, client_secret: str, token_url: str, scope: str) -> TokenManager:
    return TokenManager(client_id, client_secret, token_url, scope=scope)


_managers_lock = Lock()


def shared_token_manager(client_id: str, client_secret: str, token_url: str, scope: str = "") -> TokenManager:
    """Reuse tokens across clients in this backend process, in memory only."""
    with _managers_lock:
        return _manager(client_id, client_secret, token_url, scope)

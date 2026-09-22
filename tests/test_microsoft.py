"""Optional Microsoft mailbox connection tests with local RSA keys and a fake Entra HTTP service.

Uses the real MSAL authorization-code, PKCE, nonce and token-cache implementation.
No request reaches Microsoft, ZoomInfo, or an email recipient.
"""
import base64
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import jwt
import msal
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth_store import digest
from app.main import create_app
from app.microsoft import SCOPES
from app.store import StoreError
from config.settings import Settings
from tests.microsoft_helpers import CLIENT, MEMBER, OTHER, OWNER, TENANT, configure, identity
from tests.test_api import approve, seed, send


class EntraHTTP:
    """An OAuth provider boundary stub, while MSAL handles the actual flow and cache."""
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.oid = OWNER
        self.nonce = ""
        self.bad_nonce = False
        self.posts = []
        self.profile_id = None

    def claims(self, **updates):
        return {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT,
                "tid": TENANT, "oid": self.oid, "sub": "test-subject", "nonce": self.nonce,
                "iat": int(time.time()), "nbf": int(time.time()) - 1, "exp": int(time.time()) + 3600,
                "name": "Local Test Owner", "preferred_username": "sign-in-alias@example.com", **updates}

    def signed(self, **updates):
        return jwt.encode(self.claims(**updates), self.key, algorithm="RS256", headers={"kid": "local-test-key"})

    @staticmethod
    def response(payload, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def get(self, url, **kwargs):
        assert url.startswith("https://login.microsoftonline.com/")
        return self.response({"authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
            "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"})

    def post(self, url, data, **kwargs):
        assert url.endswith("/token")
        self.posts.append(dict(data))
        info = base64.urlsafe_b64encode(json.dumps({"uid": self.oid, "utid": TENANT}).encode()).decode().rstrip("=")
        result = {"access_token": "test-access-token", "refresh_token": "test-refresh-token", "token_type": "Bearer",
                  "scope": " ".join(SCOPES + ["openid", "profile", "offline_access"]), "expires_in": 3600, "client_info": info}
        if data["grant_type"] == "authorization_code":
            result["id_token"] = self.signed(nonce="wrong-nonce" if self.bad_nonce else self.nonce)
        return self.response(result)

    def profile(self, url, **kwargs):
        assert url == "https://graph.microsoft.com/v1.0/me"
        return self.response({"id": self.profile_id or self.oid, "displayName": "Local Test Owner" if self.oid == OWNER else "Local Test Member",
                              "mail": "owner@example.com" if self.oid == OWNER else "member@example.com"})


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    settings = configure(replace(Settings(), root_dir=tmp_path, database_path=tmp_path / "test.sqlite3",
                                 api_key="test-api-key", llm_provider="none"))
    app = create_app(settings, transport=MagicMock())
    provider = EntraHTTP()
    auth = app.state.microsoft
    monkeypatch.setattr(auth, "client", lambda cache: msal.ConfidentialClientApplication(
        CLIENT, authority=f"https://login.microsoftonline.com/{TENANT}", client_credential="test-secret",
        token_cache=cache, http_client=provider, instance_discovery=False))
    auth._jwks = SimpleNamespace(get_signing_key_from_jwt=lambda raw: SimpleNamespace(key=provider.key.public_key()))
    monkeypatch.setattr("app.microsoft.requests.get", provider.profile)
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as client:
        yield client, app, provider


def begin(client, provider):
    response = client.post("/api/v1/auth/microsoft/start")
    assert response.status_code == 200, response.text
    start = response.json()
    params = parse_qs(urlparse(start["authorization_url"]).query)
    provider.nonce = params["nonce"][0]
    return start, params


def login(client, provider, oid=OWNER):
    provider.oid = oid
    start, params = begin(client, provider)
    response = client.post("/api/v1/auth/microsoft/callback", json={"flow_handle": start["flow_handle"],
        "response": {"code": "local-test-code", "state": params["state"][0]}})
    if response.status_code == 200:
        client.headers["X-Session-Token"] = response.json()["session_token"]
    return response


def test_real_msal_pkce_mailbox_connection_encrypted_cache_and_opaque_session(workspace):
    client, app, provider = workspace
    start, params = begin(client, provider)
    assert params["code_challenge_method"] == ["S256"]
    assert set(SCOPES + ["openid", "profile", "offline_access"]) <= set(params["scope"][0].split())
    with app.state.store.connection() as db:
        flow = dict(db.execute("SELECT * FROM ms_flows").fetchone())
    assert flow["handle_hash"] == digest(start["flow_handle"])
    assert "code_verifier" not in flow["encrypted_flow"]
    payload = {"flow_handle": start["flow_handle"], "response": {"code": "local-test-code", "state": params["state"][0]}}
    response = client.post("/api/v1/auth/microsoft/callback", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mailbox"]["email"] == "owner@example.com"
    assert "role" not in result["mailbox"]
    assert "access_token" not in result and "refresh_token" not in result
    token = result["session_token"]
    assert app.state.auth_store.session(token)["token_hash"] == digest(token)
    encrypted = app.state.auth_store.cache(TENANT, OWNER)
    assert "test-refresh-token" not in encrypted
    assert "test-refresh-token" in app.state.microsoft.decrypt(encrypted)
    assert provider.posts[0]["code_verifier"]
    client.headers["X-Session-Token"] = token
    assert client.get("/api/v1/runs").status_code == 200
    assert "session_hash" not in client.get("/api/v1/auth/session").text
    assert client.post("/api/v1/auth/microsoft/callback", json=payload).status_code == 400


@pytest.mark.parametrize("attack", ["wrong-state", "wrong-nonce", "expired-flow", "wrong-mailbox"])
def test_oauth_flow_rejects_invalid_binding(workspace, attack):
    client, app, provider = workspace
    start, params = begin(client, provider)
    state = params["state"][0]
    if attack == "wrong-state":
        state = "different-browser-state"
    if attack == "wrong-nonce":
        provider.bad_nonce = True
    if attack == "wrong-mailbox":
        provider.profile_id = OTHER
    if attack == "expired-flow":
        with app.state.store.connection(write=True) as db:
            db.execute("UPDATE ms_flows SET expires_at=0")
    response = client.post("/api/v1/auth/microsoft/callback", json={"flow_handle": start["flow_handle"],
        "response": {"code": "local-test-code", "state": state}})
    assert response.status_code in (400, 502), response.text
    with app.state.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM ms_sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM ms_flows").fetchone()[0] == 0


@pytest.mark.parametrize("updates", [{"aud": OTHER}, {"tid": OTHER}, {"iss": "https://attacker.example.com"},
    {"exp": 1}, {"nbf": 9999999999}, {"oid": "invalid"}, {"tid": []}])
def test_id_token_issuer_audience_expiry_and_tenant_are_verified(workspace, updates):
    _, app, provider = workspace
    with pytest.raises(StoreError) as error:
        app.state.microsoft.validate_id_token(provider.signed(**updates))
    assert error.value.status == 401


def test_tampered_id_token_signature_is_rejected(workspace):
    _, app, provider = workspace
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(provider.claims(), other_key, algorithm="RS256")
    with pytest.raises(StoreError):
        app.state.microsoft.validate_id_token(forged)


def test_mailbox_connection_has_no_workspace_allowlist(workspace):
    client, app, provider = workspace
    assert login(client, provider, MEMBER).status_code == 200
    assert app.state.auth_store.cache(TENANT, MEMBER) is not None
    assert client.get("/api/v1/auth/session").json()["connected"] is True
    assert client.get("/api/v1/access/users").status_code == 404
    assert client.put(f"/api/v1/access/users/{OTHER}", json={"allowed": True}).status_code == 404


def test_disconnect_and_expiry_only_disable_mail(workspace):
    client, app, provider = workspace
    assert client.get("/api/v1/runs").status_code == 200
    assert login(client, provider).status_code == 200
    token = client.headers["X-Session-Token"]
    run = seed(app.state.store, count=1)
    draft = approve(client, run["emails"][0])
    assert client.delete("/api/v1/auth/session").status_code == 200
    assert client.get("/api/v1/runs").status_code == 200
    assert send(client, run, [draft]).status_code == 401
    assert app.state.auth_store.cache(TENANT, OWNER) is None
    assert app.state.auth_store.session(token) is None
    assert not app.state.store.email(draft["id"])["ready_to_send"]
    assert client.get("/api/v1/auth/session").json()["connected"] is False
    assert login(client, provider).status_code == 200
    with app.state.store.connection(write=True) as db:
        db.execute("UPDATE ms_sessions SET expires_at=0")
    assert client.get("/api/v1/runs").status_code == 200
    assert send(client, run, [draft]).status_code == 401
    app.state.email_service.transport.send.assert_not_called()


def test_approval_is_bound_to_sending_account(workspace):
    client, app, provider = workspace
    assert login(client, provider).status_code == 200
    run = seed(app.state.store, count=1)
    draft = approve(client, run["emails"][0])
    token = identity(app, MEMBER)
    client.headers["X-Session-Token"] = token
    assert send(client, run, [draft]).json()["sent_count"] == 0
    app.state.email_service.transport.send.assert_not_called()
    reapproved = approve(client, draft)
    assert send(client, run, [reapproved]).json()["sent_count"] == 1
    saved = app.state.store.email(draft["id"])
    assert saved["sent_by"] == MEMBER and saved["sender_email"] == "member@example.com"
    attempt = app.state.store.attempts(draft["id"])[0]
    assert attempt["sender_oid"] == MEMBER and attempt["provider"] == "microsoft_graph"


def test_real_msal_silent_refresh_persists_encrypted_cache(workspace):
    client, app, provider = workspace
    assert login(client, provider).status_code == 200
    auth = app.state.microsoft
    actor = auth.authenticate(client.headers["X-Session-Token"])
    # Force expiry in a genuine MSAL cache to exercise its refresh-token grant.
    cache = json.loads(auth.decrypt(app.state.auth_store.cache(TENANT, OWNER)))
    for token in cache["AccessToken"].values():
        token["expires_on"] = "1"
    app.state.auth_store.save_cache(TENANT, OWNER, auth.encrypt(json.dumps(cache)))
    assert auth.access_token(actor) == "test-access-token"
    assert provider.posts[-1]["grant_type"] == "refresh_token"
    refreshed = app.state.auth_store.cache(TENANT, OWNER)
    assert "test-refresh-token" not in refreshed
    assert all(int(token["expires_on"]) > time.time() for token in json.loads(auth.decrypt(refreshed))["AccessToken"].values())
    client.delete("/api/v1/auth/session")
    with pytest.raises(StoreError):
        auth.access_token(actor)


def test_missing_microsoft_configuration_does_not_block_workspace(workspace):
    client, app, _ = workspace
    app.state.settings.microsoft_client_secret = ""
    assert client.post("/api/v1/auth/microsoft/start").status_code == 503
    assert client.get("/api/v1/runs").status_code == 200
    assert client.post("/api/v1/auth/session", json={"password": "old-password"}).status_code == 405


def test_unknown_delivery_resolution_is_limited_to_connected_sender(workspace):
    from app.graph_mail import DeliveryError
    client, app, _ = workspace
    client.headers['X-Session-Token'] = identity(app, MEMBER)
    run = seed(app.state.store, count=1)
    draft = approve(client, run['emails'][0])
    app.state.email_service.transport.send.side_effect = DeliveryError('Acknowledgement lost', uncertain=True)
    assert send(client, run, [draft]).json()['results'][0]['status'] == 'unknown'
    member_token = client.headers['X-Session-Token']
    client.headers['X-Session-Token'] = identity(app, OTHER)
    payload = {'expected_version':draft['version'], 'delivered':False}
    assert client.post(f'/api/v1/emails/{draft["id"]}/resolve', json=payload).status_code == 403
    client.headers['X-Session-Token'] = member_token
    assert client.post(f'/api/v1/emails/{draft["id"]}/resolve', json=payload).status_code == 200


def test_old_database_migrates_without_losing_saved_drafts(workspace):
    _, app, _ = workspace
    store = app.state.store
    run = seed(store, count=1)
    draft = run['emails'][0]
    with store.connection(write=True) as db:
        for column in ('approved_by', 'sent_by', 'sender_email'):
            db.execute(f'ALTER TABLE emails DROP COLUMN {column}')
        for column in ('sender_oid', 'sender_email', 'provider'):
            db.execute(f'ALTER TABLE attempts DROP COLUMN {column}')
        db.execute("UPDATE emails SET status='ready',ready_to_send=1,approved_version=version")
    store.initialize()
    migrated = store.email(draft['id'])
    assert migrated['body'] == draft['body'] and migrated['status'] == 'draft'
    assert migrated['version'] == draft['version'] + 1 and not migrated['ready_to_send']
    assert migrated['approved_by'] is None
    store.initialize()
    assert store.email(draft['id'])['version'] == migrated['version']


def test_openapi_only_requires_mailbox_for_email_actions(workspace):
    _, app, _ = workspace
    schema = app.openapi()
    assert schema['paths']['/api/v1/runs']['get']['security'] == [{'BackendKey':[]}]
    assert schema['paths']['/api/v1/emails/send-ready']['post']['security'] == [{'BackendKey':[], 'MicrosoftMailbox':[]}]
    assert schema['paths']['/api/v1/emails/{email_id}/resolve']['post']['security'] == [{'BackendKey':[], 'MicrosoftMailbox':[]}]
    assert 'X-Session-Token' in schema['paths']['/api/v1/emails/{email_id}/approval']['post']['description']
    assert schema['paths']['/api/v1/auth/microsoft/start']['post']['security'] == [{'BackendKey':[]}]

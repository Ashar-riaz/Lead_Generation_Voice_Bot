"""Local mailbox test identities; never contacts Microsoft."""
from contextlib import contextmanager
from dataclasses import replace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
OWNER = "33333333-3333-4333-8333-333333333333"
MEMBER = "44444444-4444-4444-8444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"


def configure(settings):
    settings.microsoft_tenant_id = TENANT
    settings.microsoft_client_id = CLIENT
    settings.microsoft_client_secret = "not-a-real-microsoft-secret"
    settings.microsoft_redirect_uri = "http://localhost:3000/api/auth/microsoft/callback"
    settings.token_encryption_key = Fernet.generate_key().decode()
    return settings


def identity(app, oid=OWNER):
    auth, store = app.state.microsoft, app.state.auth_store
    store.record_identity(TENANT, oid, "Test Owner" if oid == OWNER else "Test Member", "owner@example.com" if oid == OWNER else "member@example.com")
    token = store.new_session(TENANT, oid, auth.encrypt('{}'))
    return token


@contextmanager
def microsoft_client(app):
    configure(app.state.settings)
    with TestClient(app, headers={"X-API-Key": app.state.settings.api_key}) as client:
        token = identity(app)
        client.headers["X-Session-Token"] = token
        client.actor = app.state.microsoft.authenticate(token)
        yield client

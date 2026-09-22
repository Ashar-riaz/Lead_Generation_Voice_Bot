"""Direct workspace access without Microsoft credentials; mail still needs consent."""
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from config.settings import Settings
from tests.test_api import seed, send


@pytest.fixture
def workspace(tmp_path):
    settings = replace(Settings(), root_dir=tmp_path, database_path=tmp_path / "app.sqlite3",
                       api_key="test-no-login-key", microsoft_tenant_id="", microsoft_client_id="",
                       microsoft_client_secret="", token_encryption_key="", llm_provider="none",
                       zoominfo_client_id="", zoominfo_client_secret="")
    app = create_app(settings, transport=MagicMock())
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as client:
        yield client, app


def test_research_and_draft_editing_work_without_microsoft(workspace, monkeypatch):
    client, app = workspace
    run = seed(app.state.store, count=1)
    for path in ("settings", "runs", f'runs/{run["id"]}', "leads", "emails", "knowledge",
                 f'runs/{run["id"]}/export', f'emails/{run["emails"][0]["id"]}/attempts'):
        assert client.get(f"/api/v1/{path}").status_code == 200
    settings = client.get("/api/v1/settings").json()
    assert settings["login_required"] is False and settings["microsoft_configured"] is False
    assert client.get("/api/v1/auth/session").json() == {"connected": False, "configured": False, "mailbox": None}
    draft = run["emails"][0]
    fields = {key: draft[key] for key in ("to_email", "to_name", "subject", "body")}
    fields["subject"] = "Reviewed without login"
    updated = client.patch(f'/api/v1/emails/{draft["id"]}', json={**fields, "expected_version": draft["version"]})
    assert updated.status_code == 200 and updated.json()["ready_to_send"] is False
    assert updated.json()["subject"] == fields["subject"]
    assert client.post("/api/v1/runs", json={"query": "AI training"}).status_code == 503  # ZoomInfo missing, not login.
    app.state.settings.zoominfo_client_id = "provider-test-id"
    app.state.settings.zoominfo_client_secret = "provider-test-secret"
    research = MagicMock()
    monkeypatch.setattr("app.main.run_search", research)
    assert client.post("/api/v1/runs", json={"query": "AI training", "country": "United Kingdom"}).status_code == 202
    research.assert_called_once()
    app.state.email_service.transport.send.assert_not_called()


@pytest.mark.parametrize("token", ["", "bad-token", "x" * 43])
def test_missing_or_stale_mailbox_never_blocks_research_or_enables_sending(workspace, token):
    client, app = workspace
    client.headers["X-Session-Token"] = token
    run = seed(app.state.store, count=1)
    draft = run["emails"][0]
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/auth/session").json()["connected"] is False
    response = client.post(f'/api/v1/emails/{draft["id"]}/approval', json={"expected_version": 1, "ready_to_send": True})
    assert response.status_code in (401, 503)
    assert send(client, run, [draft]).status_code in (401, 503)
    assert not app.state.store.email(draft["id"])["ready_to_send"]
    assert app.state.store.attempts(draft["id"]) == []
    app.state.email_service.transport.send.assert_not_called()


def test_approval_can_be_removed_without_reconnecting(workspace):
    client, app = workspace
    draft = seed(app.state.store, count=1)["emails"][0]
    approved = app.state.store.approve(draft["id"], draft["version"], True, actor_id="old-mailbox-id")
    response = client.post(f'/api/v1/emails/{draft["id"]}/approval', json={"expected_version": approved["version"], "ready_to_send": False})
    assert response.status_code == 200
    assert not response.json()["ready_to_send"] and response.json()["approved_by"] is None


def test_backend_key_is_still_required_without_dashboard_login(workspace):
    client, _ = workspace
    for path in ("settings", "runs", "leads", "emails", "knowledge", "auth/session"):
        assert client.get(f"/api/v1/{path}", headers={"X-API-Key": ""}).status_code == 401

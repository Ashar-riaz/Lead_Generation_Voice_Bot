"""API integration tests. Every mail transport is fake; no message leaves these tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import SendReady
from app.services import DeliveryError, EmailService
from app.store import Store, StoreError
from tests.microsoft_helpers import microsoft_client
from config.settings import Settings


@pytest.fixture
def setup(tmp_path):
    settings = replace(Settings(), root_dir=tmp_path, database_path=tmp_path / "app.sqlite3",
                       output_dir=tmp_path / "outputs", api_key="api-test-key", llm_provider="none",
                       sender_email="sender@example.com", daily_send_limit=30)
    transport = MagicMock()
    app = create_app(settings, transport=transport)
    with microsoft_client(app) as client:
        yield client, app.state.store, transport, settings


def seed(store, count=2, mock=False):
    run = store.create_run({"query": "AI training", "mock": mock})
    # create_run's public request contract is validated by HTTP, not this test helper.
    leads, drafts = [], []
    for i in range(count):
        company = f"Example Company {i}"
        leads.append({"company_id": str(i), "company_name": company, "website": "example.com", "score": 90,
                      "tier": "Hot", "signals": [{"topic": "AI training", "signal_score": 95, "signal_date": "2026-09-15"}], "contacts": [], "score_reasons": ["Strong intent"]})
        drafts.append({"company_id": str(i), "company_name": company, "to_name": "Test Person",
                       "to_email": f"person{i}@example.com", "subject": "Training for your team", "body": "Hi there,\n\nWould AI training be useful for your team?", "programme": "AI"})
    store.finish_run(run["id"], {"leads": leads, "drafts": drafts})
    return store.run(run["id"], full=True)


def approve(client, email, ready=True):
    response = client.post(f'/api/v1/emails/{email["id"]}/approval', json={"expected_version": email["version"], "ready_to_send": ready})
    assert response.status_code == 200, response.text
    return response.json()


def send(client, run, emails):
    return client.post("/api/v1/emails/send-ready", json={"run_id": run["id"], "emails": [{"email_id": e["id"], "expected_version": e["version"]} for e in emails]})


def test_health_public_and_all_private_endpoints_require_key(setup):
    client, _, _, _ = setup
    assert client.get("/health", headers={"X-API-Key": ""}).status_code == 200
    assert client.get("/api/v1/runs", headers={"X-API-Key": "wrong"}).status_code == 401
    response = client.post("/api/v1/emails/send-ready", headers={"X-API-Key": "wrong"}, json={"run_id": "x", "emails": [{"email_id": "x", "expected_version": 1}]})
    assert response.status_code == 401
    assert "fake" not in client.get("/api/v1/settings").text


def test_missing_api_key_fails_closed(setup):
    client, _, _, settings = setup
    settings.api_key = ""
    assert client.get("/api/v1/runs").status_code == 503


def test_sample_search_is_removed_and_legacy_samples_cannot_send(setup):
    client, store, transport, _ = setup
    response = client.post("/api/v1/runs", json={"query": "AI training", "mock": True})
    assert response.status_code == 422
    assert client.get("/api/v1/lookups/intent-topics?mock=true").status_code == 422
    run = seed(store, mock=True)
    approved = approve(client, run["emails"][0])
    assert send(client, run, [approved]).status_code == 409
    assert client.get("/api/v1/runs").json()["total"] == 0
    transport.send.assert_not_called()


@pytest.mark.parametrize("extra", [{"send": True}, {"limit": 0}, {"limit": 1000}, {"mock": "false"}, {"query": " "}, {"min_tier": "unknown"}])
def test_invalid_search_or_send_flag_rejected(setup, extra):
    client, _, transport, _ = setup
    response = client.post("/api/v1/runs", json={"query": "AI training", **extra})
    assert response.status_code == 422
    transport.send.assert_not_called()


def test_unapproved_drafts_never_reach_mail_transport(setup):
    client, store, transport, _ = setup
    run = seed(store)
    response = send(client, run, run["emails"])
    assert response.status_code == 200
    assert response.json()["sent_count"] == 0
    assert all(r["status"] == "skipped" for r in response.json()["results"])
    transport.send.assert_not_called()


def test_only_selected_approved_email_sent_and_repeat_click_does_not_resend(setup):
    client, store, transport, _ = setup
    run = seed(store)
    approved = approve(client, run["emails"][0])
    response = send(client, run, [approved, run["emails"][1]])
    assert response.json()["sent_count"] == 1
    assert transport.send.call_count == 1
    saved = store.email(approved["id"])
    assert saved["status"] == "sent" and not saved["ready_to_send"] and saved["sent_at"] and saved["message_id"]
    assert send(client, run, [approved]).json()["sent_count"] == 0
    assert transport.send.call_count == 1


def test_revoke_approval_and_stale_send_request(setup):
    client, store, transport, _ = setup
    run = seed(store)
    approved = approve(client, run["emails"][0])
    revoked = approve(client, approved, False)
    assert revoked["status"] == "draft"
    assert send(client, run, [approved]).json()["sent_count"] == 0
    transport.send.assert_not_called()


def test_edit_resets_approval_and_checks_revision(setup):
    client, store, transport, _ = setup
    run = seed(store)
    approved = approve(client, run["emails"][0])
    edit = {k: approved[k] for k in ("to_email", "to_name", "subject", "body")}
    edit.update(subject="Updated subject", expected_version=approved["version"])
    response = client.patch(f'/api/v1/emails/{approved["id"]}', json=edit)
    assert response.status_code == 200
    changed = response.json()
    assert not changed["ready_to_send"] and changed["approved_version"] is None
    assert changed["version"] > approved["version"]
    assert client.patch(f'/api/v1/emails/{approved["id"]}', json=edit).status_code == 409
    assert send(client, run, [approved]).json()["sent_count"] == 0
    transport.send.assert_not_called()


def test_approval_requires_literal_boolean(setup):
    client, store, _, _ = setup
    email = seed(store)["emails"][0]
    for value in ("false", "true", 1, "yes"):
        assert client.post(f'/api/v1/emails/{email["id"]}/approval', json={"expected_version": 1, "ready_to_send": value}).status_code == 422


def test_header_injection_and_bad_addresses_rejected(setup):
    client, store, _, _ = setup
    email = seed(store)["emails"][0]
    base = {k: email[k] for k in ("to_email", "to_name", "subject", "body")}
    for edit in ({"to_email": "invalid"}, {"subject": "Hello\r\nBcc: other@example.com"}, {"to_name": "Name\nInjected"}, {"body": " "}):
        assert client.patch(f'/api/v1/emails/{email["id"]}', json={**base, **edit, "expected_version": 1}).status_code == 422


def test_suppression_and_daily_limit(setup):
    client, store, transport, settings = setup
    run = seed(store)
    emails = [approve(client, e) for e in run["emails"]]
    suppression = settings.root_dir / "data" / "suppression.txt"
    suppression.parent.mkdir(parents=True, exist_ok=True)
    suppression.write_text(emails[0]["to_email"].upper())
    assert send(client, run, [emails[0]]).json()["sent_count"] == 0
    settings.daily_send_limit = 0
    assert send(client, run, [emails[1]]).json()["sent_count"] == 0
    transport.send.assert_not_called()


def test_legacy_cli_send_log_respected(setup):
    import json
    client, store, transport, settings = setup
    run = seed(store)
    email = approve(client, run["emails"][0])
    log = settings.root_dir / "data" / "send_log.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"2026-01-01": [email["to_email"]]}))
    assert send(client, run, [email]).json()["sent_count"] == 0
    transport.send.assert_not_called()


def test_duplicate_recipient_blocked_across_runs_and_restart(setup):
    client, store, transport, settings = setup
    first = seed(store)
    assert send(client, first, [approve(client, first["emails"][0])]).json()["sent_count"] == 1
    second = seed(store)
    approved = approve(client, second["emails"][0])
    reopened = Store(settings.database_path)
    reopened.initialize()
    service = EmailService(reopened, settings, transport)
    result = service.send_ready(SendReady(run_id=second["id"], emails=[{"email_id": approved["id"], "expected_version": approved["version"]}]), actor=client.actor)
    assert result["sent_count"] == 0 and transport.send.call_count == 1


def test_failed_delivery_requires_new_approval(setup):
    client, store, transport, _ = setup
    run = seed(store)
    email = approve(client, run["emails"][0])
    transport.send.side_effect = DeliveryError("Authentication failed")
    assert send(client, run, [email]).json()["results"][0]["status"] == "failed"
    assert not store.email(email["id"])["ready_to_send"]
    assert send(client, run, [email]).json()["sent_count"] == 0
    transport.send.side_effect = None
    approved = approve(client, store.email(email["id"]))
    assert send(client, run, [approved]).json()["sent_count"] == 1


def test_uncertain_delivery_blocks_retry_until_manual_resolution(setup):
    client, store, transport, _ = setup
    run = seed(store)
    email = approve(client, run["emails"][0])
    transport.send.side_effect = DeliveryError("Microsoft acknowledgement lost", uncertain=True)
    assert send(client, run, [email]).json()["results"][0]["status"] == "unknown"
    assert client.post(f'/api/v1/emails/{email["id"]}/approval', json={"expected_version": email["version"], "ready_to_send": True}).status_code == 409
    resolved = client.post(f'/api/v1/emails/{email["id"]}/resolve', json={"expected_version": email["version"], "delivered": False}).json()
    assert resolved["status"] == "failed" and not resolved["ready_to_send"]
    transport.send.side_effect = None
    assert send(client, run, [approve(client, resolved)]).json()["sent_count"] == 1


def test_concurrent_send_requests_reserve_only_once(setup):
    client, store, _, settings = setup
    run = seed(store)
    email = approve(client, run["emails"][0])
    started, release = Event(), Event()
    transport = MagicMock()
    def slow_send(*args):
        started.set()
        assert release.wait(5)
    transport.send.side_effect = slow_send
    service = EmailService(store, settings, transport)
    request = SendReady(run_id=run["id"], emails=[{"email_id": email["id"], "expected_version": email["version"]}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.send_ready, request, client.actor)
        assert started.wait(5)
        try:
            second = pool.submit(service.send_ready, request, client.actor).result(timeout=5)
            assert second["sent_count"] == 0
        finally:
            release.set()
        assert first.result(timeout=5)["sent_count"] == 1
    assert transport.send.call_count == 1


def test_restart_does_not_retry_interrupted_send(setup):
    client, store, transport, settings = setup
    run = seed(store)
    email = approve(client, run["emails"][0])
    store.claim_send(email["id"], run["id"], email["version"], 30, set(), set(), set(), actor=client.actor)
    store.recover_interrupted()
    assert store.email(email["id"])["status"] == "unknown"
    assert send(client, run, [email]).json()["sent_count"] == 0
    transport.send.assert_not_called()


def test_invalid_run_scope_and_missing_ids(setup):
    client, store, transport, _ = setup
    first, second = seed(store), seed(store)
    email = approve(client, first["emails"][0])
    assert send(client, second, [email]).json()["sent_count"] == 0
    assert client.get("/api/v1/runs/missing").status_code == 404
    assert client.get("/api/v1/leads/missing").status_code == 404
    assert client.get("/api/v1/emails/missing").status_code == 404
    transport.send.assert_not_called()


def test_filters_and_export_include_current_email_approval(setup):
    client, store, _, _ = setup
    run = seed(store)
    approve(client, run["emails"][0])
    assert client.get(f'/api/v1/leads?run_id={run["id"]}&tier=Hot&q=Company').json()["total"] == 2
    assert client.get(f'/api/v1/emails?run_id={run["id"]}&status=ready').json()["total"] == 1
    response = client.get(f'/api/v1/runs/{run["id"]}/export')
    assert response.status_code == 200 and "True,ready" in response.text
    assert "attachment" in response.headers["content-disposition"]


def test_knowledge_update_used_for_next_load(setup):
    import shutil
    client, _, _, settings = setup
    target = settings.root_dir / "knowledge"
    shutil.copytree(settings.knowledge_dir, target)
    settings.knowledge_dir = target
    profile = "WTD offers practical training for employees. Contact our team to discuss programme suitability."
    assert client.put("/api/v1/knowledge", json={"profile": profile}).status_code == 200
    assert client.get("/api/v1/knowledge").json()["profile"] == profile


def test_cli_cannot_bypass_mailbox_approval(setup):
    from src.output.sender import EmailSender
    from src.models import EmailDraft
    _, _, _, settings = setup
    sender = EmailSender(settings)
    draft = EmailDraft("Example", "Test", "person@example.com", "Hello", "Test message")
    for approved in (False, True):
        with pytest.raises(RuntimeError, match="Microsoft mailbox connection"):
            sender.send_all([draft], approved=approved, delay_seconds=0)

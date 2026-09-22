"""Shared mailbox, saved contact corrections and transcript-grounded call reviews.

All provider calls are mocked; these tests never send mail, spend ZoomInfo credits,
place a call or contact an AI service.
"""
import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.graph_mail import MicrosoftMailTransport
from app.mail_conversations import GraphConversationClient
from app.main import create_app
from app.store import StoreError
from config.settings import Settings
from src.zoominfo.client import ZoomInfoClient
from tests.microsoft_helpers import microsoft_client
from tests.test_api import approve, seed, send
from tests.test_conversations import incoming
from tests.test_voice_calls import workspace as voice_workspace, placed, prepare, approval, start, webhook


@pytest.fixture
def shared(tmp_path, monkeypatch):
    settings = replace(Settings(), root_dir=tmp_path, database_path=tmp_path / "shared.sqlite3",
                       api_key="test-key", llm_provider="none", microsoft_mailbox_address="info@wbt.org.uk",
                       microsoft_mailbox_user_id="", zoominfo_client_id="test", zoominfo_client_secret="test")
    transport = MagicMock()
    app = create_app(settings, transport=transport)
    app.state.microsoft.access_token = MagicMock(return_value="fake-token")
    get = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr("app.microsoft.requests.get", get)
    with microsoft_client(app) as client:
        yield client, app, transport, get


def test_shared_identity_is_distinct_from_connecting_account_and_scopes(shared):
    client, app, _, _ = shared
    mailbox = client.get("/api/v1/auth/session").json()["mailbox"]
    assert mailbox["email"] == "info@wbt.org.uk"
    assert mailbox["account_email"] == "owner@example.com" and mailbox["shared"]
    assert set(app.state.microsoft.scopes) == {"User.Read", "Mail.Read.Shared", "Mail.Send.Shared"}
    assert client.get("/api/v1/settings").json()["login_required"] is False
    result = client.post("/api/v1/mailbox/check")
    assert result.status_code == 200 and result.json()["read_access"]
    assert "Send As" in result.json()["message"]
    assert "/users/info%40wbt.org.uk/mailFolders/inbox" in shared[3].call_args.args[0]


def test_denied_shared_access_blocks_mail_but_not_dashboard(shared):
    client, app, transport, get = shared
    run = seed(app.state.store, 1)
    get.return_value.status_code = 403
    result = client.post(f'/api/v1/emails/{run["emails"][0]["id"]}/approval',
                         json={"expected_version": 1, "ready_to_send": True})
    assert result.status_code == 403 and "Full Access" in result.json()["detail"]
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/runs", headers={"X-Session-Token": ""}).status_code == 200
    assert client.post("/api/v1/mailbox/check").status_code == 403
    transport.send.assert_not_called()


def test_sending_mailbox_is_bound_to_approval(shared):
    client, app, transport, _ = shared
    run = seed(app.state.store, 1)
    row = approve(client, run["emails"][0])
    assert row["approved_mailbox"].startswith("shared:info@wbt.org.uk")
    app.state.settings.microsoft_mailbox_address = "different@example.com"
    result = send(client, run, [row]).json()
    assert result["sent_count"] == 0 and "mailbox changed" in result["results"][0]["detail"]
    transport.send.assert_not_called()
    app.state.settings.microsoft_mailbox_address = "info@wbt.org.uk"
    assert send(client, run, [row]).json()["sent_count"] == 1
    assert app.state.store.email(row["id"])["sender_email"] == "info@wbt.org.uk"


def test_shared_transport_and_threaded_reply_use_same_mailbox(shared, monkeypatch):
    _, app, _, _ = shared
    actor = shared[0].actor
    post = MagicMock(return_value=MagicMock(status_code=202))
    monkeypatch.setattr("app.graph_mail.requests.post", post)
    draft = {"subject": "Reviewed", "body": "Reviewed text", "to_name": "Alex", "to_email": "person@example.com"}
    MicrosoftMailTransport(app.state.microsoft, actor).send(draft, "wtd-test")
    assert post.call_args.args[0].endswith("/users/info%40wbt.org.uk/sendMail")
    assert post.call_args.kwargs["json"]["message"]["from"]["emailAddress"]["address"] == "info@wbt.org.uk"
    assert post.call_args.kwargs["json"]["saveToSentItems"] is True
    graph = GraphConversationClient(app.state.microsoft, actor)
    graph.get = MagicMock(return_value={"value": []})
    graph.conversation_page("conversation-id")
    assert "/users/info%40wbt.org.uk/messages" in graph.get.call_args.args[0]
    graph.reply("id/with+symbols", [{"address": "person@example.com"}], "My reviewed response")
    assert post.call_args.args[0].endswith("/users/info%40wbt.org.uk/messages/id%2Fwith%2Bsymbols/reply")
    assert post.call_args.kwargs["json"]["message"]["body"]["content"] == "My reviewed response"
    assert post.call_count == 2


def test_shared_conversations_check_current_access_even_for_cached_messages(shared):
    client, app, _, get = shared
    run = seed(app.state.store, 1)
    row = approve(client, run["emails"][0])
    send(client, run, [row])
    graph = MagicMock()
    graph.find_original.return_value = ({"conversationId": "thread-123", "from": {"emailAddress": {"address": "info@wbt.org.uk"}},
        "toRecipients": [{"emailAddress": {"address": row["to_email"]}}]}, None)
    graph.conversation_page.return_value = {"value": [incoming()]}
    app.state.conversations.transport_factory = lambda actor: graph
    base = f'/api/v1/emails/{row["id"]}/conversation'
    assert client.post(base + "/sync").status_code == 200
    assert client.get(base).json()["messages"][0]["direction"] == "inbound"
    get.return_value.status_code = 403
    assert client.get(base).status_code == 403
    get.return_value.status_code = 200
    app.state.settings.microsoft_mailbox_address = "different@example.com"
    assert client.get(base).status_code == 403


def test_graph_rejects_continuation_into_other_mailbox(shared):
    graph = GraphConversationClient(shared[1].state.microsoft, shared[0].actor)
    for url in ["https://evil.example/v1.0/messages", "https://graph.microsoft.com/v1.0/me/messages",
                "https://graph.microsoft.com/v1.0/users/other%40example.com/messages"]:
        with pytest.raises(StoreError):
            graph.get(url)


def test_company_enrichment_exact_id_and_phone_field(shared):
    client = ZoomInfoClient(shared[1].state.settings)
    client._request = MagicMock(return_value={"data": [{"type": "Company", "id": "123", "meta": {"matchStatus": "FULL_MATCH"},
                                                        "attributes": {"phone": "+442079460001"}}]})
    assert client.company_phone("123") == "+442079460001"
    args, kwargs = client._request.call_args
    assert args == ("POST", "/data/v1/companies/enrich")
    assert kwargs["body"]["data"] == {"type": "CompanyEnrich", "attributes": {
        "matchCompanyInput": [{"companyId": 123}], "outputFields": ["id", "phone"]}}
    for row in [{"type": "Company", "id": "999", "attributes": {"phone": "+442079460001"}},
                {"type": "Company", "id": "123", "meta": {"matchStatus": "NO_MATCH"}},
                {"type": "Company", "id": "123", "attributes": {}}]:
        client._request.return_value = {"data": [row]}
        assert client.company_phone("123") == ""


def test_saved_contact_persists_validates_versions_and_does_not_change_email(shared):
    client, app, mail, _ = shared
    run = seed(app.state.store, 1)
    lead = run["leads"][0]
    base = f'/api/v1/leads/{lead["id"]}'
    values = {"expected_version": 1, "company_phone": "+442079460001", "contact_name": "Alex Green", "contact_phone": "+442079460002"}
    result = client.patch(base + "/contact", json=values)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["contact_version"] == 2 and saved["company_phone_source"] == "manual"
    assert saved["contacts"][0]["first_name"] == "Alex Green"
    assert client.get(base).json()["contacts"][0]["phone"] == values["contact_phone"]
    assert client.patch(base + "/contact", json=values).status_code == 409
    assert client.patch(base + "/contact", json={**values, "expected_version": 2, "company_phone": "02079460001"}).status_code == 422
    assert app.state.store.email(run["emails"][0]["id"])["to_name"] == "Test Person"
    assert client.patch(base + "/contact", json=values, headers={"X-API-Key": ""}).status_code == 401
    mail.send.assert_not_called()


def test_lookup_preserves_manual_number_and_does_not_invent_missing_phone(shared):
    client, app, _, _ = shared
    lead = seed(app.state.store, 1)["leads"][0]
    base = f'/api/v1/leads/{lead["id"]}'
    provider = MagicMock()
    provider.company_phone.return_value = "+442079460002"
    app.state.contacts.provider_factory = lambda: provider
    values = {"expected_version": 1, "company_phone": "+442079460001"}
    assert client.patch(base + "/contact", json=values).status_code == 200
    result = client.post(base + "/company-phone", json={"expected_version": 2})
    assert result.status_code == 200, result.text
    saved = result.json()["lead"]
    assert saved["company_phone"] == "+442079460001"
    assert saved["zoominfo_company_phone"] == "+442079460002"
    provider.company_phone.return_value = ""
    result = client.post(base + "/company-phone", json={"expected_version": 3})
    assert "no company phone" in result.json()["message"]
    assert result.json()["lead"]["company_phone"] == "+442079460001"
    assert client.post(base + "/company-phone", json={"expected_version": 3}).status_code == 409
    assert provider.company_phone.call_count == 2


def test_contact_correction_invalidates_prepared_call(voice_workspace):
    client, _, transport, run = voice_workspace
    row = approval(client, prepare(voice_workspace))
    result = client.patch(f'/api/v1/leads/{run["leads"][0]["id"]}/contact', json={
        "expected_version": 1, "company_phone": "+442079460003", "contact_name": "New Contact"})
    assert result.status_code == 200
    assert start(client, row).status_code == 409
    assert client.get(f'/api/v1/voice/calls/{row["id"]}').json()["status"] == "superseded"
    transport.make_outbound_call.assert_not_called()


def assessment(quote="Yes, we would like AI training for our team."):
    return {"interest": "interested", "confidence": "high", "summary": "The person expressed interest in AI training.",
            "reasoning": "They explicitly requested training.", "evidence": [{"turn": 1, "quote": quote}],
            "training_needs": ["AI training"], "objections": [], "follow_up": "Review the conversation before arranging a follow-up."}


def finish_call(workspace, heard):
    row = placed(workspace)
    webhook(workspace, row, "outbound-answer")
    if heard:
        webhook(workspace, row, "gather", {"SpeechResult": heard}, turn=1)
    assert webhook(workspace, row, "status", {"CallStatus": "completed", "SequenceNumber": "4"}).status_code == 204
    return row


def test_summary_is_persisted_and_duplicate_callback_does_not_reanalyse(voice_workspace):
    client, app, transport, _ = voice_workspace
    app.state.voice.analysis.generate.return_value = assessment()
    row = finish_call(voice_workspace, assessment()["evidence"][0]["quote"])
    data = client.get(f'/api/v1/voice/calls/{row["id"]}').json()
    assert data["analysis"]["status"] == "completed"
    assert data["analysis"]["result"]["interest"] == "interested"
    assert data["transcript"][1]["heard"] == assessment()["evidence"][0]["quote"]
    webhook(voice_workspace, row, "status", {"CallStatus": "completed", "SequenceNumber": "4"})
    client.post(f'/api/v1/voice/calls/{row["id"]}/analysis')
    app.state.voice.analysis.generate.assert_called_once()
    assert transport.make_outbound_call.call_count == 1
    app.state.voice.analysis.initialize()
    assert client.get(f'/api/v1/voice/calls/{row["id"]}').json()["analysis"]["result"]["interest"] == "interested"


@pytest.mark.parametrize("quote", ["I want to buy training.", "Which team skills would you like to improve?"])
def test_unheard_or_ai_only_evidence_is_rejected(voice_workspace, quote):
    client, app, _, _ = voice_workspace
    app.state.voice.analysis.generate.return_value = assessment(quote)
    row = finish_call(voice_workspace, "Hello, who is calling?")
    data = client.get(f'/api/v1/voice/calls/{row["id"]}').json()["analysis"]
    assert data["status"] == "failed" and data["result"] is None
    app.state.voice.analysis.generate.return_value = {**assessment(), "interest": "unclear", "confidence": "low", "evidence": []}
    assert client.post(f'/api/v1/voice/calls/{row["id"]}/analysis').json()["analysis"]["status"] == "completed"


def test_silence_is_not_disinterest_and_opt_out_survives_ai_outage(voice_workspace):
    client, app, _, _ = voice_workspace
    app.state.voice.analysis.generate.side_effect = RuntimeError("provider unavailable")
    row = finish_call(voice_workspace, "")
    result = client.get(f'/api/v1/voice/calls/{row["id"]}').json()["analysis"]["result"]
    assert result["interest"] == "no_conversation"
    row = finish_call(voice_workspace, "Please don't call this number again.")
    result = client.get(f'/api/v1/voice/calls/{row["id"]}').json()["analysis"]["result"]
    assert result["interest"] == "not_interested" and result["do_not_call"]
    app.state.voice.analysis.generate.assert_not_called()


def test_active_call_cannot_be_analysed_or_accessed_without_key(voice_workspace):
    row = placed(voice_workspace)
    path = f'/api/v1/voice/calls/{row["id"]}/analysis'
    assert voice_workspace[0].post(path).status_code == 409
    assert voice_workspace[0].post(path, headers={"X-API-Key": ""}).status_code == 401
    voice_workspace[1].state.voice.analysis.generate.assert_not_called()
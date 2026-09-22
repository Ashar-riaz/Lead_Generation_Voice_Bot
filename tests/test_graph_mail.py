"""Graph transport boundaries; every HTTP call is mocked and no email is sent."""
from unittest.mock import MagicMock

import pytest
import requests

from app.graph_mail import DeliveryError, MicrosoftMailTransport

DRAFT = {"to_email": "person@example.com", "to_name": "Test Person", "subject": "Reviewed subject", "body": "Reviewed body"}


def test_delegated_send_preserves_exact_approved_content(monkeypatch):
    post = MagicMock(return_value=MagicMock(status_code=202))
    monkeypatch.setattr("app.graph_mail.requests.post", post)
    auth = MagicMock()
    auth.access_token.return_value = "local-test-token"
    actor = {"object_id": "owner-id"}
    MicrosoftMailTransport(auth, actor).send(DRAFT, "wtd-test-reference")
    auth.access_token.assert_called_once_with(actor)
    args, kwargs = post.call_args
    assert args == ("https://graph.microsoft.com/v1.0/me/sendMail",)
    assert kwargs["headers"]["Authorization"] == "Bearer local-test-token"
    message = kwargs["json"]["message"]
    assert message["subject"] == DRAFT["subject"] and message["body"]["content"] == DRAFT["body"]
    assert message["toRecipients"] == [{"emailAddress": {"address": DRAFT["to_email"], "name": DRAFT["to_name"]}}]
    assert "from" not in message and "bccRecipients" not in message
    assert message["internetMessageHeaders"] == [{"name": "x-wtd-delivery-id", "value": "wtd-test-reference"}]
    assert kwargs["json"]["saveToSentItems"] is True and kwargs["allow_redirects"] is False


@pytest.mark.parametrize("outcome,uncertain", [(401, False), (403, False), (429, False), (400, False),
    (500, True), (503, True), (302, True), (requests.ConnectTimeout(), False), (requests.ReadTimeout(), True), (requests.ConnectionError(), True)])
def test_no_automatic_retry_and_ambiguous_outcomes_are_held(monkeypatch, outcome, uncertain):
    post = MagicMock()
    if isinstance(outcome, Exception):
        post.side_effect = outcome
    else:
        post.return_value.status_code = outcome
    monkeypatch.setattr("app.graph_mail.requests.post", post)
    with pytest.raises(DeliveryError) as error:
        MicrosoftMailTransport(MagicMock(), {}).send(DRAFT, "wtd-test-reference")
    assert error.value.uncertain is uncertain
    assert post.call_count == 1


def test_revoked_mail_access_cannot_issue_a_send_request(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr("app.graph_mail.requests.post", post)
    auth = MagicMock()
    auth.access_token.side_effect = RuntimeError("revoked")
    with pytest.raises(DeliveryError) as error:
        MicrosoftMailTransport(auth, {}).send(DRAFT, "wtd-test-reference")
    assert not error.value.uncertain
    post.assert_not_called()

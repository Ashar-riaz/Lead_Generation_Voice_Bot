"""Send an explicitly approved message from the connected Microsoft mailbox."""
import requests
from app.mailbox_target import graph_mailbox_path


class DeliveryError(Exception):
    def __init__(self, message: str, uncertain=False):
        self.uncertain = uncertain
        super().__init__(message)


class MicrosoftMailTransport:
    def __init__(self, auth, actor: dict):
        self.auth, self.actor = auth, actor

    def send(self, draft: dict, message_id: str):
        try:
            token = self.auth.access_token(self.actor)
        except Exception:
            raise DeliveryError("Microsoft mail access is unavailable. Reconnect your mailbox in Connections before reviewing and retrying.") from None
        # The target comes only from backend configuration, never a browser-supplied From.
        body = {"message": {
            "subject": draft["subject"], "body": {"contentType": "Text", "content": draft["body"]},
            "toRecipients": [{"emailAddress": {"address": draft["to_email"], "name": draft["to_name"]}}],
            "internetMessageHeaders": [{"name": "x-wtd-delivery-id", "value": message_id}],
        }, "saveToSentItems": True}
        if self.actor.get("shared"):
            body["message"]["from"] = {"emailAddress": {"address": self.actor["email"]}}
        try:
            response = requests.post("https://graph.microsoft.com/v1.0" + graph_mailbox_path(self.actor) + "/sendMail", json=body,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        timeout=(10, 30), allow_redirects=False)
        except requests.ConnectTimeout:
            raise DeliveryError("Could not connect to Microsoft Graph. Review and approve again to retry.") from None
        except requests.RequestException:
            raise DeliveryError("Microsoft's send acknowledgement was lost. Check Outlook Sent Items before resolving delivery.", uncertain=True) from None
        if response.status_code == 202:
            return
        if response.status_code in (401, 403):
            raise DeliveryError("Microsoft denied mail sending. For a shared mailbox, ask your administrator for Full Access, Send As and delegated Mail.Send.Shared, then reconnect. For a personal mailbox, check Mail.Send.")
        if response.status_code == 429:
            raise DeliveryError("Microsoft rate limited this request. Wait before reviewing and approving a retry.")
        if 400 <= response.status_code < 500:
            raise DeliveryError(f"Microsoft rejected the email (HTTP {response.status_code}). Check the recipient and mailbox.")
        # A server/proxy error could happen after accepting the message. Never retry blindly.
        raise DeliveryError("Microsoft did not confirm delivery. Check Outlook Sent Items or Exchange message trace before retrying.", uncertain=True)
"""Microsoft Graph conversation reads and explicitly approved threaded replies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

import requests
from email_validator import EmailNotValidError, validate_email

from app.graph_mail import DeliveryError
from app.store import StoreError

GRAPH = "https://graph.microsoft.com/v1.0"
SELECT = "id,conversationId,internetMessageId,internetMessageHeaders,subject,from,sender,toRecipients,replyTo,body,receivedDateTime,sentDateTime,isDraft"


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if not self.hidden and tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def addresses(recipients):
    result = []
    if not isinstance(recipients, list) or len(recipients) > 20:
        return []
    for item in recipients:
        email = item.get("emailAddress", {}) if isinstance(item, dict) else {}
        try:
            address = validate_email(email.get("address", ""), check_deliverability=False).normalized
        except (EmailNotValidError, TypeError):
            return []
        if not any(r["address"].lower() == address.lower() for r in result):
            result.append({"address": address, "name": str(email.get("name") or "")[:200]})
    return result


def normalise_message(message, mailbox_addresses):
    sender = addresses([message.get("from") or message.get("sender") or {}])
    recipients = addresses(message.get("toRecipients", []))
    # Reply-To can deliberately differ from From; show and approve the exact recipients.
    reply_to = addresses(message["replyTo"]) if message.get("replyTo") else sender
    headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))
               for h in message.get("internetMessageHeaders", []) if isinstance(h, dict)}
    outbound = bool(sender and sender[0]["address"].lower() in mailbox_addresses)
    body = message.get("body") or {}
    content = str(body.get("content") or "")
    truncated = len(content) > 100000
    content = content[:100000]
    if str(body.get("contentType", "")).lower() == "html":
        parser = PlainText()
        parser.feed(content)
        content = "".join(parser.parts)
    return {"provider_id": message["id"], "conversation_id": message["conversationId"],
            "internet_message_id": message.get("internetMessageId", ""),
            "subject": str(message.get("subject") or "")[:1000],
            "from_address": sender[0]["address"] if sender else "",
            "from_name": sender[0]["name"] if sender else "", "to": recipients, "reply_to": reply_to,
            "body": content.strip(), "body_truncated": truncated,
            "direction": "outbound" if outbound else "inbound",
            "timestamp": message.get("receivedDateTime") or message.get("sentDateTime") or "",
            "can_reply": bool(not outbound and not message.get("isDraft") and reply_to
                              and headers.get("auto-submitted", "no").lower() == "no")}


class GraphConversationClient:
    def __init__(self, auth, actor):
        self.token = auth.access_token(actor)

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'}

    def get(self, url, params=None):
        parsed = urlparse(url)
        # nextLink is opaque, but it must never send the bearer token to another host.
        if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
            raise StoreError(502, "Microsoft returned an invalid mailbox continuation link.")
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise StoreError(502, "Could not read Microsoft email. Your saved messages are still available; try refreshing later.") from None
        if response.status_code in (401, 403):
            raise StoreError(403, "Microsoft denied mailbox reading. Add delegated Mail.Read permission and reconnect the mailbox in Connections.")
        if response.status_code == 404:
            raise StoreError(409, "The email is no longer available in this mailbox. Refresh the conversation before replying.")
        if response.status_code == 429:
            raise StoreError(429, "Microsoft limited mailbox requests. Wait a minute before refreshing again.")
        if response.status_code != 200:
            raise StoreError(502, "Microsoft could not return the email conversation. Try again later.")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError:
            raise StoreError(502, "Microsoft returned an unreadable email response.") from None

    def find_original(self, delivery_id, attempted_at, cursor=None):
        since = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
        lower = (since - timedelta(minutes=5)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        upper = (since + timedelta(days=7)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        page = self.get(cursor or GRAPH + "/me/mailFolders/sentitems/messages", None if cursor else {
            "$select": "id,conversationId,internetMessageHeaders,from,toRecipients,isDraft",
            "$filter": f"sentDateTime ge {lower} and sentDateTime le {upper}",
            "$orderby": "sentDateTime asc", "$top": "100"})
        matches = [m for m in page.get("value", []) if not m.get("isDraft") and any(
            str(h.get("name", "")).lower() == "x-wtd-delivery-id" and h.get("value") == delivery_id
            for h in m.get("internetMessageHeaders", []))]
        if len(matches) > 1:
            raise StoreError(409, "More than one sent message has this delivery reference. Check Outlook before linking this conversation.")
        return (matches[0] if matches else None), page.get("@odata.nextLink")

    def conversation_page(self, conversation_id, cursor=None):
        escaped = conversation_id.replace("'", "''")
        return self.get(cursor or GRAPH + "/me/messages", None if cursor else {
            "$filter": f"receivedDateTime ge 1970-01-01T00:00:00Z and conversationId eq '{escaped}'",
            "$orderby": "receivedDateTime asc", "$select": SELECT, "$top": "50"})

    def message(self, provider_id):
        return self.get(GRAPH + "/me/messages/" + quote(provider_id, safe=""), {"$select": SELECT})

    def reply(self, provider_id, recipients, body):
        url = GRAPH + "/me/messages/" + quote(provider_id, safe="") + "/reply"
        # Exact reviewed recipients and plain text; never use Reply All or hidden recipients.
        payload = {"message": {"body": {"contentType": "Text", "content": body},
                   "toRecipients": [{"emailAddress": r} for r in recipients],
                   "ccRecipients": [], "bccRecipients": []}}
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=(5, 30), allow_redirects=False)
        except requests.ConnectTimeout:
            raise DeliveryError("Could not connect to Microsoft. Review and approve the reply again before retrying.") from None
        except requests.RequestException:
            raise DeliveryError("Microsoft did not confirm this reply. Check Outlook Sent Items before resolving its status.", uncertain=True) from None
        if response.status_code == 202:
            return
        if response.status_code in (401, 403):
            raise DeliveryError("Microsoft denied sending. Check delegated Mail.Send permission and reconnect the mailbox.")
        if 400 <= response.status_code < 500:
            raise DeliveryError(f"Microsoft rejected the reply (HTTP {response.status_code}). Refresh the conversation, review and approve again.")
        raise DeliveryError("Microsoft did not confirm this reply. Check Outlook Sent Items before resolving its status.", uncertain=True)

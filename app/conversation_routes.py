"""Email replies remain private to the connected sending mailbox."""
from __future__ import annotations

import threading
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field

from app.conversation_store import ConversationStore
from app.graph_mail import DeliveryError
from app.mail_conversations import GraphConversationClient, addresses, normalise_message
from app.schemas import Approval, DeliveryResolution, RequestModel
from app.store import StoreError


class NewReply(RequestModel):
    message_id: UUID


class ReplyVersion(RequestModel):
    expected_version: int = Field(ge=1)


class ReplyEdit(ReplyVersion):
    body: str = Field(min_length=1, max_length=20000)


class ConversationController:
    def __init__(self, app, settings, store, microsoft):
        self.app, self.settings, self.store = app, settings, store
        self.data = ConversationStore(store)
        self.transport_factory = lambda actor: GraphConversationClient(microsoft, actor)
        self._locks = [threading.Lock() for _ in range(64)]

    def sync(self, email_id, actor):
        with self._locks[int(email_id[:8], 16) % len(self._locks)]:
            email, thread = self.data.ensure(email_id, actor)
            transport = self.transport_factory(actor)
            if not thread["conversation_id"]:
                original_attempt = next((a for a in self.store.attempts(email_id)
                                         if a["message_id"] == email["message_id"]), None)
                original, cursor = transport.find_original(email["message_id"],
                    original_attempt["created_at"] if original_attempt else email["sent_at"], thread["scan_cursor"])
                if original:
                    if (not original.get("conversationId") or email["to_email"].lower() not in
                            {r["address"].lower() for r in addresses(original.get("toRecipients", []))}):
                        raise StoreError(409, "The sent message does not match the original reviewed recipient.")
                self.data.discovery(email_id, original, cursor)
                if not original:
                    return self.data.view(email_id, actor)
                _, thread = self.data.ensure(email_id, actor)
            page = transport.conversation_page(thread["conversation_id"], thread["message_cursor"])
            mailbox_addresses = {actor["email"].lower(), (email["sender_email"] or "").lower(),
                                 (thread["from_address"] or "").lower()}
            messages = []
            for message in page.get("value", []):
                if message.get("conversationId") != thread["conversation_id"]:
                    raise StoreError(502, "Microsoft returned a message from another conversation. Nothing was imported.")
                if not message.get("isDraft"):
                    messages.append(normalise_message(message, mailbox_addresses))
            self.data.save_messages(email_id, messages, page.get("@odata.nextLink"))
            return self.data.view(email_id, actor)

    def send(self, email_id, reply_id, actor, version):
        row = self.data.reply(email_id, reply_id, actor)
        if row["status"] in ("sent", "sending", "unknown"):
            return row  # A repeated request never reissues the POST to Microsoft.
        if row["status"] != "ready" or row["version"] != version or row["approved_version"] != version:
            raise StoreError(409, "Save and approve this exact reply before sending.")
        email, thread = self.data.ensure(email_id, actor)
        with self.store.connection() as db:
            target = self.data.message(email_id, row["message_id"], db)
        transport = self.transport_factory(actor)
        # Recheck the actual message before reserving a send: no client-supplied Graph IDs or recipients.
        fresh = normalise_message(transport.message(target["provider_id"]),
            {actor["email"].lower(), (email["sender_email"] or "").lower(), (thread["from_address"] or "").lower()})
        if (fresh["provider_id"] != target["provider_id"] or fresh["conversation_id"] != thread["conversation_id"]
                or not fresh["can_reply"] or fresh["reply_to"] != row["recipients"]):
            raise StoreError(409, "The reply destination changed. Refresh, save and review the reply again before sending.")
        emails = self.app.state.email_service
        _, legacy_today = emails.legacy_history()
        claim, reserved = self.data.claim(email_id, reply_id, actor, version, self.settings.daily_send_limit,
                                           emails.suppression(), legacy_today)
        if not reserved:
            return claim
        try:
            transport.reply(target["provider_id"], claim["recipients"], claim["body"])
        except DeliveryError as exc:
            self.data.finish(reply_id, claim["attempt_id"], "unknown" if exc.uncertain else "failed", str(exc))
        except Exception:
            self.data.finish(reply_id, claim["attempt_id"], "unknown", "Reply delivery was not confirmed. Check Outlook Sent Items before resolving.")
        else:
            self.data.finish(reply_id, claim["attempt_id"], "sent")
        return self.data.reply(email_id, reply_id, actor)


def install_conversations(app, settings, store, microsoft, connected_mailbox):
    controller = ConversationController(app, settings, store, microsoft)
    app.state.conversations = controller
    router = APIRouter(prefix="/api/v1/emails", tags=["Email conversations"])

    @router.get("/{email_id}/conversation")
    def conversation(email_id: UUID, actor: dict = Depends(connected_mailbox)):
        return controller.data.view(email_id.hex, actor)

    @router.post("/{email_id}/conversation/sync")
    def sync(email_id: UUID, actor: dict = Depends(connected_mailbox)):
        return controller.sync(email_id.hex, actor)

    @router.post("/{email_id}/replies", status_code=201)
    def draft(email_id: UUID, payload: NewReply, actor: dict = Depends(connected_mailbox)):
        return controller.data.draft(email_id.hex, payload.message_id.hex, actor)

    @router.patch("/{email_id}/replies/{reply_id}")
    def edit(email_id: UUID, reply_id: UUID, payload: ReplyEdit, actor: dict = Depends(connected_mailbox)):
        return controller.data.edit(email_id.hex, reply_id.hex, actor, payload.expected_version, payload.body)

    @router.post("/{email_id}/replies/{reply_id}/approval")
    def approve(email_id: UUID, reply_id: UUID, payload: Approval, actor: dict = Depends(connected_mailbox)):
        return controller.data.approve(email_id.hex, reply_id.hex, actor, payload.expected_version, payload.ready_to_send)

    @router.post("/{email_id}/replies/{reply_id}/send")
    def send(email_id: UUID, reply_id: UUID, payload: ReplyVersion, actor: dict = Depends(connected_mailbox)):
        return controller.send(email_id.hex, reply_id.hex, actor, payload.expected_version)

    @router.post("/{email_id}/replies/{reply_id}/resolve")
    def resolve(email_id: UUID, reply_id: UUID, payload: DeliveryResolution, actor: dict = Depends(connected_mailbox)):
        return controller.data.resolve(email_id.hex, reply_id.hex, actor, payload.expected_version, payload.delivered)

    app.include_router(router)

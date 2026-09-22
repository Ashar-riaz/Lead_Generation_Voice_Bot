"""Reuse the existing agents for research; deliver only database-approved drafts."""
from __future__ import annotations

import json
import logging

from config.settings import Settings
from src.graph.builder import build_graph, initial_state
from src.graph.state import LeadGenDeps
from src.zoominfo.client import ZoomInfoClient, ZoomInfoError
from src.zoominfo.auth import ZoomInfoAuthError

from app.schemas import DraftEdit, SendReady
from app.graph_mail import DeliveryError, MicrosoftMailTransport
from app.store import Store, StoreError, now

log = logging.getLogger(__name__)


def run_search(store: Store, settings: Settings, run_id: str, request: dict):
    state = {"progress": [], "errors": []}
    try:
        store.progress(run_id, state)
        if request.get("mock"):
            raise ValueError("Sample mode has been removed from the web API")
        client = ZoomInfoClient(settings)
        deps = LeadGenDeps.build(settings, client)
        options = {k: v for k, v in request.items() if k not in ("query", "mock")}
        options["send"] = False  # No HTTP search can enter the CLI's send branch.
        state = initial_state(request["query"], **options)
        for chunk in build_graph().stream(state.copy(), context=deps, stream_mode="updates"):
            for update in chunk.values():
                for key, value in (update or {}).items():
                    if key in ("progress", "errors"):
                        # The frontend does not need local export paths.
                        if key == "progress":
                            value = ["export_results: files saved" if v.startswith("export_results:") else v for v in value]
                        state[key] = state.get(key, []) + value
                    else:
                        state[key] = value
                store.progress(run_id, state)
        store.finish_run(run_id, state)
    except Exception as exc:
        log.exception("Search %s failed", run_id)
        if isinstance(exc, (ZoomInfoError, ZoomInfoAuthError)):
            message = str(exc)
        elif isinstance(exc, ValueError):
            message = str(exc)
        else:
            message = "The search failed. Check the backend log and provider configuration, then start a new search."
        store.progress(run_id, state, status="failed", error=message)


class EmailService:
    def __init__(self, store: Store, settings: Settings, transport=None, auth=None):
        self.store, self.settings = store, settings
        self.transport, self.auth = transport, auth

    def suppression(self) -> set[str]:
        file = self.settings.root_dir / "data" / "suppression.txt"
        return {v.strip().lower() for v in file.read_text(encoding="utf-8").splitlines() if v.strip() and not v.lstrip().startswith("#")} if file.exists() else set()

    def legacy_history(self):
        file = self.settings.root_dir / "data" / "send_log.json"
        try:
            history = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
            return ({e.strip().lower() for emails in history.values() for e in emails},
                    {e.strip().lower() for e in history.get(now()[:10], [])})
        except (ValueError, TypeError, AttributeError):
            raise StoreError(409, "The CLI send log is unreadable. Repair data/send_log.json before sending.")

    def send_ready(self, request: SendReady, actor: dict | None = None):
        if not actor:
            raise StoreError(401, "A connected Microsoft mailbox is required to send emails")
        run = self.store.run(request.run_id)
        if run["mock"]:
            raise StoreError(409, "Sample searches cannot send real emails. Run a live search first.")
        transport = self.transport or MicrosoftMailTransport(self.auth, actor)
        results = []
        for item in request.emails:
            try:
                # Validate provider/LLM-generated fields as well as user edits.
                row = self.store.email(item.email_id)
                validated = DraftEdit(expected_version=row["version"], to_email=row["to_email"], to_name=row["to_name"], subject=row["subject"], body=row["body"])
                if row["to_email"].strip().lower() != str(validated.to_email).lower():
                    raise StoreError(409, "Save the recipient as a plain email address, then approve the draft again.")
                past, today = self.legacy_history()
                claim = self.store.claim_send(item.email_id, request.run_id, item.expected_version,
                                              self.settings.daily_send_limit, self.suppression(), past, today, actor=actor)
            except StoreError as exc:
                results.append({"email_id": item.email_id, "status": "skipped", "detail": exc.detail})
                continue
            except ValueError:
                results.append({"email_id": item.email_id, "status": "skipped", "detail": "Invalid recipient, subject or body. Edit and approve the draft again."})
                continue
            try:
                transport.send(claim, claim["message_id"])
            except DeliveryError as exc:
                status = "unknown" if exc.uncertain else "failed"
                self.store.finish_send(claim, status, str(exc))
                results.append({"email_id": item.email_id, "status": status, "detail": str(exc)})
            except Exception:
                # A transport implementation may have sent before raising.
                message = "Delivery status is unresolved. Check Outlook Sent Items or Exchange message trace before retrying."
                self.store.finish_send(claim, "unknown", message)
                results.append({"email_id": item.email_id, "status": "unknown", "detail": message})
            else:
                self.store.finish_send(claim, "sent")
                results.append({"email_id": item.email_id, "status": "sent", "detail": "Accepted by Microsoft Graph", "message_id": claim["message_id"]})
        return {"results": results, "sent_count": sum(r["status"] == "sent" for r in results)}

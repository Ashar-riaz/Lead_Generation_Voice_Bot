"""API-key protected workspace; optional Microsoft connection for email sending."""
from __future__ import annotations

import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.openapi.utils import get_openapi

from config.settings import Settings, settings as default_settings
from src.knowledge.loader import load_knowledge
from src.zoominfo.client import ZoomInfoClient, ZoomInfoError
from src.zoominfo.auth import ZoomInfoAuthError

from app.schemas import Approval, DeliveryResolution, DraftEdit, KnowledgeEdit, MicrosoftCallback, RunCreate, SendReady
from app.auth_store import AuthStore
from app.microsoft import MicrosoftAuth
from app.responses import EmailList, EmailView, LeadList, LeadView, RunDetail, RunList, RunSummary, SendResponse
from app.services import EmailService, run_search
from app.store import Store, StoreError
from app.voice_routes import install_voice
from app.conversation_routes import install_conversations
from app.mailbox_target import mailbox_key
from app.contact_routes import install_contacts


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or default_settings
    store = Store(settings.database_path)
    auth_store = AuthStore(store)
    microsoft = MicrosoftAuth(settings, auth_store)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        auth_store.initialize()
        store.recover_interrupted()
        app.state.voice_store.initialize()
        app.state.voice.analysis.initialize()
        app.state.conversations.data.initialize()
        yield

    app = FastAPI(title="WTD Lead Generation API", version="3.1.0", lifespan=lifespan,
                  description="Workspace routes require X-API-Key. Microsoft login is not required for research or editing. Approving, sending and resolving email require a connected Microsoft mailbox via X-Session-Token.")
    app.state.store = store
    app.state.settings = settings
    app.state.microsoft = microsoft
    app.state.auth_store = auth_store
    app.state.email_service = EmailService(store, settings, transport, auth=microsoft)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type", "X-API-Key", "X-Session-Token"])

    @app.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    key_header = APIKeyHeader(name="X-API-Key", scheme_name="BackendKey", auto_error=False)
    session_header = APIKeyHeader(name="X-Session-Token", scheme_name="MicrosoftMailbox", auto_error=False)

    def authenticate(key: str | None = Depends(key_header)):
        if not settings.api_key:
            raise StoreError(503, "API_KEY is not configured. Run python setup_local.py and restart the backend.")
        if not key or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
            raise StoreError(401, "Invalid or missing API key")

    def connected_mailbox(token: str | None = Depends(session_header), _=Depends(authenticate)):
        actor = microsoft.authenticate(token)
        if actor.get("shared"):
            microsoft.verify_mailbox_access(actor)
        return actor

    auth_api = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(authenticate)], tags=["Microsoft mailbox connection"])
    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @auth_api.post("/microsoft/start")
    def microsoft_start():
        return microsoft.start()

    @auth_api.post("/microsoft/callback")
    def microsoft_callback(payload: MicrosoftCallback):
        return microsoft.complete(payload.flow_handle, payload.response)

    @auth_api.get("/session")
    def session_info(token: str | None = Depends(session_header)):
        try:
            user = microsoft.authenticate(token)
        except StoreError as exc:
            if exc.status not in (401, 503):
                raise
            return {"connected": False, "configured": settings.has_microsoft, "mailbox": None}
        return {"connected": True, "configured": True, "mailbox": microsoft.public_user(user)}

    @auth_api.delete("/session")
    def logout(token: str | None = Depends(session_header)):
        if token:
            microsoft.logout(token)
        return {"connected": False, "mailbox": None}

    @api.post("/mailbox/check", tags=["Microsoft mailbox connection"])
    def check_mailbox(token: str | None = Depends(session_header)):
        return microsoft.verify_mailbox_access(microsoft.authenticate(token))

    @app.get("/health", tags=["Health"])
    def health():
        return {"status": "ok", "version": "3.1.0"}

    @api.get("/settings", tags=["Configuration"])
    def public_settings():
        return {"zoominfo_configured": settings.has_zoominfo, "microsoft_configured": settings.has_microsoft,
                "mail_provider": "microsoft_graph", "login_required": False,
                "mailbox_address": settings.microsoft_mailbox_address,
                "llm_provider": settings.llm_provider,
                "llm_configured": (settings.llm_provider == "gemini" and bool(settings.gemini_api_key)) or (settings.llm_provider == "anthropic" and bool(settings.anthropic_api_key)),
                "daily_send_limit": settings.daily_send_limit, "default_country": settings.default_country,
                "sender_name": settings.sender_name, "sender_email": settings.sender_email,
                "approval_required": True}

    @api.post("/runs", status_code=202, tags=["Searches"], response_model=RunSummary)
    def create_run(payload: RunCreate, tasks: BackgroundTasks):
        if payload.mock:
            raise StoreError(422, "Sample mode has been removed. Use live ZoomInfo search.")
        if not payload.mock and not settings.has_zoominfo:
            raise StoreError(503, "Set ZOOMINFO_CLIENT_ID and ZOOMINFO_CLIENT_SECRET in .env to use live search.")
        request = payload.model_dump(mode="json")
        run = store.create_run(request)
        tasks.add_task(run_search, store, settings, run["id"], request)
        return run

    @api.get("/runs", tags=["Searches"], response_model=RunList)
    def list_runs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return store.list_runs(limit, offset, live_only=True)

    @api.get("/runs/{run_id}", tags=["Searches"], response_model=RunDetail)
    def get_run(run_id: str):
        return store.run(run_id, full=True)

    @api.get("/runs/{run_id}/export", tags=["Searches"])
    def export(run_id: str, format: Literal["csv", "json"] = "csv"):
        run = store.run(run_id, full=True)
        if format == "json":
            return JSONResponse(run, headers={"Content-Disposition": f'attachment; filename="wtd-{run["id"]}.json"'})
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(["Data source", "Company", "Website", "Tier", "Score", "Intent topics", "Evidence dates", "Recipient", "Subject", "Email body", "Ready to send", "Status", "Sent at"])
        emails = {e["lead_id"]: e for e in run["emails"]}
        def cell(v):
            value = str(v if v is not None else "")
            # Keep exported text from becoming a spreadsheet formula.
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
        for lead in run["leads"]:
            email = emails.get(lead["id"], {})
            writer.writerow([cell(v) for v in ["Fictional sample" if run["mock"] else "ZoomInfo", lead["company_name"], lead["website"], lead["tier"], lead["score"],
                "; ".join(s["topic"] for s in lead["signals"]), "; ".join(s["signal_date"] for s in lead["signals"]),
                email.get("to_email"), email.get("subject"), email.get("body"), email.get("ready_to_send", False), email.get("status", "No draft"), email.get("sent_at")]])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="wtd-{run["id"]}.csv"'})

    @api.get("/leads", tags=["Leads"], response_model=LeadList)
    def list_leads(run_id: str | None = None, tier: Literal["Cool", "Warm", "Hot"] | None = None,
                   q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return store.list_leads(run_id, tier, q, limit, offset)

    @api.get("/leads/{lead_id}", tags=["Leads"], response_model=LeadView)
    def get_lead(lead_id: str):
        return store.lead(lead_id)

    @api.get("/emails", tags=["Email review"], response_model=EmailList)
    def list_emails(run_id: str | None = None, status: Literal["draft", "ready", "sending", "sent", "failed", "unknown"] | None = None,
                    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return store.list_emails(run_id, status, limit, offset)

    @api.post("/emails/send-ready", tags=["Email delivery"], response_model=SendResponse)
    def send_ready(payload: SendReady, user: dict = Depends(connected_mailbox)):
        return app.state.email_service.send_ready(payload, actor=user)

    @api.get("/emails/{email_id}", tags=["Email review"], response_model=EmailView)
    def get_email(email_id: str):
        return store.email(email_id)

    @api.patch("/emails/{email_id}", tags=["Email review"], response_model=EmailView)
    def edit_email(email_id: str, payload: DraftEdit):
        return store.edit_email(email_id, payload.model_dump())

    @api.post("/emails/{email_id}/approval", tags=["Email review"], response_model=EmailView)
    def approve_email(email_id: str, payload: Approval, token: str | None = Depends(session_header)):
        # Removing approval must remain possible after a mailbox expires or disconnects.
        user = microsoft.authenticate(token) if payload.ready_to_send else None
        if user and user.get("shared"):
            microsoft.verify_mailbox_access(user)
        return store.approve(email_id, payload.expected_version, payload.ready_to_send,
                             actor_id=user["object_id"] if user else None, mailbox=mailbox_key(user) if user else None)

    @api.get("/emails/{email_id}/attempts", tags=["Email delivery"])
    def attempts(email_id: str):
        return {"items": store.attempts(email_id)}

    @api.post("/emails/{email_id}/resolve", tags=["Email delivery"], response_model=EmailView)
    def resolve(email_id: str, payload: DeliveryResolution, user: dict = Depends(connected_mailbox)):
        """Use only AFTER checking Outlook Sent Items or Exchange message trace to confirm the outcome of an unknown delivery."""
        row = store.email(email_id)
        if row["sent_by"] != user["object_id"]:
            raise StoreError(403, "Connect the sending mailbox to resolve this delivery.")
        return store.resolve(email_id, payload.expected_version, payload.delivered)

    @api.get("/knowledge", tags=["Company knowledge"])
    def knowledge():
        kb = load_knowledge(settings.knowledge_dir)
        return {"profile": kb.profile, "categories": kb.categories}

    @api.put("/knowledge", tags=["Company knowledge"])
    def update_knowledge(payload: KnowledgeEdit):
        target = settings.knowledge_dir / "company_profile.md"
        temp = target.with_name(f".profile-{uuid4().hex}.tmp")
        try:
            temp.write_text(payload.profile, encoding="utf-8")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        load_knowledge.cache_clear()
        return knowledge()

    @api.get("/lookups/{field}", tags=["ZoomInfo"])
    def lookups(field: Literal["intent-topics", "countries", "states", "metro-regions", "industries", "employee-count", "revenue-ranges", "tech-products", "management-levels"],
                mock: bool = False, q: str = Query("", max_length=200), refresh: bool = False):
        if mock:
            raise StoreError(422, "Sample mode has been removed. Use live ZoomInfo lookups.")
        if not mock and not settings.has_zoominfo:
            raise StoreError(503, "ZoomInfo credentials are missing")
        client = ZoomInfoClient(settings)
        try:
            rows = client.lookup(field, use_cache=not refresh)
        except (ZoomInfoError, ZoomInfoAuthError) as exc:
            raise StoreError(502, str(exc)) from None
        return {"items": [r for r in rows if q.lower() in r.get("attributes", {}).get("name", "").lower()]}

    @api.post("/zoominfo/test-connection", tags=["ZoomInfo"])
    def test_zoominfo_connection():
        """Check OAuth and a fresh topic lookup; does not enrich contacts or send email."""
        if not settings.has_zoominfo:
            raise StoreError(503, "Set ZOOMINFO_CLIENT_ID and ZOOMINFO_CLIENT_SECRET from a Standard App, then restart the backend.")
        try:
            client = ZoomInfoClient(settings)
            topics = client.lookup("intent-topics", use_cache=False)
            return {"status": "connected", "intent_topic_count": len(topics), **client.tokens.status(),
                    "message": "OAuth and intent topic lookup succeeded. Search and enrichment remain subject to your ZoomInfo subscription."}
        except (ZoomInfoError, ZoomInfoAuthError) as exc:
            raise StoreError(502, str(exc)) from None

    install_voice(app, settings, store, authenticate)
    install_contacts(app, settings, store, authenticate)
    install_conversations(app, settings, store, microsoft, connected_mailbox)
    app.include_router(auth_api)
    app.include_router(api)

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
            # Only mail delivery requires both headers. Approval removal needs only the API key.
            for path, methods in schema["paths"].items():
                if path.startswith("/api/v1/") and not path.startswith("/api/v1/auth/"):
                    for operation in methods.values():
                        if isinstance(operation, dict) and "responses" in operation:
                            conversations = "/conversation" in path or "/replies" in path
                            mail_required = path == "/api/v1/mailbox/check" or (path.startswith("/api/v1/emails/") and (conversations or path.endswith(("/send-ready", "/resolve"))))
                            operation["security"] = [{"BackendKey": [], **({"MicrosoftMailbox": []} if mail_required else {})}]
                            if path.startswith("/api/v1/emails/") and path.endswith("/approval") and not conversations:
                                operation["description"] = "Setting ready_to_send=true also requires X-Session-Token for the sending mailbox. Removing approval requires only X-API-Key."
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app


app = create_app()
"""Private approval APIs and signature-verified public Twilio voice webhooks."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import Field, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.schemas import RequestModel
from app.store import StoreError
from app.twilio_voice_service import VoiceSettings, TwilioVoiceService, normalise_phone
from app.voice_agent import VoiceAgent, build_context
from app.voice_call_context_service import VoiceCallContextService, TERMINAL


class CallPlan(RequestModel):
    lead_id: str = Field(min_length=1, max_length=64)
    to_number: str = Field(min_length=8, max_length=40)
    contact_name: str = Field(default="", max_length=120)
    purpose: str = Field(min_length=10, max_length=700)


class Revision(RequestModel):
    expected_version: int = Field(ge=1)


class CallApproval(Revision):
    ready_to_call: StrictBool


class CallResolution(RequestModel):
    verified_not_placed: StrictBool


class VoiceController:
    def __init__(self, store, settings, voice_settings):
        self.store, self.settings, self.config = store, settings, voice_settings
        self.contexts = VoiceCallContextService(store)
        self.agent = VoiceAgent(settings)
        self.transport_factory = lambda: TwilioVoiceService(self.config)
        self._locks = [threading.Lock() for _ in range(64)]

    def public(self, row):
        return {key: row[key] for key in ("id", "lead_id", "to_number", "from_number", "status", "version",
                "ready_to_call", "call_sid", "last_error", "created_at")} | {
            "contact_name": row["context"]["contact_name"], "purpose": row["context"]["call_purpose"],
            "company_name": row["context"]["prospect"]["company_name"],
            "training_focus": row["context"]["training_focus"], "voice": self.config.voice,
            "greeting": self.agent.greeting(row["context"]), "transcript": self.contexts.transcript(row["id"])}

    def start(self, call_id, version):
        self.config.validate()
        if not self.agent.available:
            raise StoreError(503, "Configure Gemini or Anthropic in .env before starting a voice conversation.")
        # Build and validate transport before consuming the saved approval.
        transport = self.transport_factory()
        row, claimed = self.contexts.claim(call_id, version, self.config.daily_limit, normalise_phone(self.config.phone_number))
        if not claimed:
            return self.public(row)
        try:
            call = transport.make_outbound_call(row["to_number"], call_id)
            self.contexts.bind(call_id, call.sid, row["to_number"], row["from_number"])
            row = self.contexts.status(call_id, "queued")
        except TwilioRestException as exc:
            # A 5xx could follow acceptance; no automatic POST retry in either case.
            uncertain = not (400 <= exc.status < 500)
            detail = f"Twilio rejected this call (HTTP {exc.status}, code {exc.code}). Check your Twilio configuration."
            if uncertain:
                detail = "Twilio did not confirm call creation. Check Twilio call logs before resolving it."
            row = self.contexts.creation_error(call_id, detail, uncertain)
        except Exception:
            row = self.contexts.creation_error(call_id, "Call creation was not confirmed. Do not redial until you check Twilio call logs.", True)
        return self.public(row)

    def twiml(self, call_id, turn, text, end=False):
        response = VoiceResponse()
        if end:
            response.say(text, voice=self.config.voice, language="en-GB")
            response.hangup()
        else:
            gather = response.gather(input="speech", action=self.config.url("gather", call_id, turn + 1),
                                     method="POST", language="en-GB", speech_timeout="auto",
                                     timeout=5, action_on_empty_result=True)
            gather.say(text, voice=self.config.voice, language="en-GB")
            response.hangup()
        return str(response)

    def conversation(self, call_id, turn, heard=""):
        # Bounded lock stripes serialize retries. Persistent turn responses survive restart.
        with self._locks[int(call_id[:8], 16) % len(self._locks)]:
            cached = self.contexts.turn_response(call_id, turn)
            if cached:
                return cached
            row = self.contexts.get_call_context(call_id)
            history = self.contexts.transcript(call_id)
            if row["status"] in TERMINAL or (history and history[-1]["end_call"]):
                return self.twiml(call_id, turn, "Thank you. Goodbye.", True)
            if turn != len(history):
                return self.twiml(call_id, turn, "I'm sorry, we couldn't continue this call. Goodbye.", True)
            opt_out = False
            if turn == 0:
                self.contexts.status(call_id, "in-progress")
                reply, end = self.agent.greeting(row["context"]), False
            elif self.agent.stop_request(heard):
                reply, end, opt_out = self.agent.stop_request(heard)
            elif turn >= self.config.max_turns or time.time() - row["attempted_at"] > self.config.max_seconds - 10:
                reply, end = "Thank you for speaking with WTD. Our team can review this conversation and any follow-up request. Goodbye.", True
            else:
                reply, end, opt_out = self.agent.reply(row["context"], history, heard)
            xml = self.twiml(call_id, turn, reply, end)
            return self.contexts.save_turn(call_id, turn, heard, reply, xml, end, opt_out)


def install_voice(app, settings, store, authenticate):
    controller = VoiceController(store, settings, VoiceSettings.from_env())
    app.state.voice = controller
    app.state.voice_store = controller.contexts
    api = APIRouter(prefix="/api/v1/voice", tags=["Voice calls"], dependencies=[Depends(authenticate)])
    webhooks = APIRouter(prefix="/voice", tags=["Twilio webhooks"])

    @api.get("/settings")
    def voice_settings():
        try:
            controller.config.validate()
            configured, detail = True, ""
        except (StoreError, ValueError) as exc:
            configured, detail = False, getattr(exc, "detail", "Check voice settings in .env.")
        return {"configured": configured, "detail": detail, "llm_configured": controller.agent.available,
                "voice": controller.config.voice, "language": "en-GB", "daily_limit": controller.config.daily_limit}

    @api.post("/plans", status_code=201)
    def prepare(payload: CallPlan):
        controller.config.validate()
        lead = store.lead(payload.lead_id)
        if store.run(lead["run_id"])["mock"]:
            raise StoreError(409, "Sample leads cannot receive real calls.")
        number = normalise_phone(payload.to_number)
        context = build_context(store, settings, payload.lead_id, payload.contact_name, payload.purpose)
        row = controller.contexts.save_call_context(payload.lead_id, context, number, normalise_phone(controller.config.phone_number))
        return controller.public(row)

    @api.get("/leads/{lead_id}")
    def lead_calls(lead_id: str):
        store.lead(lead_id)
        return {"items": [controller.public(row) for row in controller.contexts.list_calls(lead_id)]}

    @api.get("/calls/{call_id}")
    def get_call(call_id: UUID):
        return controller.public(controller.contexts.get_call_context(call_id.hex))

    @api.post("/calls/{call_id}/approval")
    def approve(call_id: UUID, payload: CallApproval):
        return controller.public(controller.contexts.approve(call_id.hex, payload.expected_version, payload.ready_to_call))

    @api.post("/calls/{call_id}/start")
    def start(call_id: UUID, payload: Revision):
        return controller.start(call_id.hex, payload.expected_version)

    @api.post("/calls/{call_id}/sync")
    def sync(call_id: UUID):
        row = controller.contexts.get_call_context(call_id.hex)
        if not row["call_sid"]:
            raise StoreError(409, "No Twilio SID is available yet. Check the callback configuration or Twilio logs.")
        try:
            call = controller.transport_factory().fetch_call(row["call_sid"])
        except Exception:
            raise StoreError(502, "Could not check the call with Twilio. No new call was placed.") from None
        controller.contexts.bind(row["id"], call.sid, call.to, call._from)
        return controller.public(controller.contexts.status(row["id"], call.status))

    @api.post("/calls/{call_id}/resolve")
    def resolve(call_id: UUID, payload: CallResolution):
        if payload.verified_not_placed is not True:
            raise StoreError(422, "Only resolve this after verifying in Twilio that no call was placed.")
        return controller.public(controller.contexts.resolve_not_placed(call_id.hex))

    async def verified_form(request: Request, call_id: str):
        config = controller.config
        if not config.auth_token:
            raise StoreError(503, "Twilio is not configured.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
            raise StoreError(415, "Expected a Twilio form webhook.")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 65536:
                raise StoreError(413, "Webhook too large.")
        try:
            form = FormData(parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=100))
        except (ValueError, UnicodeDecodeError):
            raise StoreError(400, "Invalid webhook form.") from None
        # Use the configured external origin, never untrusted proxy/Host headers.
        url = config.public_base_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        signature = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(config.auth_token).validate(url, form, signature):
            raise StoreError(403, "Invalid Twilio signature.")
        if form.get("AccountSid") != config.account_sid or not re.fullmatch(r"CA[0-9a-fA-F]{32}", form.get("CallSid", "")):
            raise StoreError(403, "Invalid Twilio account or call.")
        controller.contexts.bind(call_id, form["CallSid"], form.get("To", ""), form.get("From", ""))
        return form

    def xml_response(xml):
        return Response(xml, media_type="application/xml", headers={"Cache-Control": "no-store"})

    @webhooks.post("/outbound-answer/{call_id}")
    async def answer(call_id: UUID, request: Request):
        await verified_form(request, call_id.hex)
        return xml_response(await run_in_threadpool(controller.conversation, call_id.hex, 0))

    @webhooks.post("/gather/{call_id}")
    async def gather(call_id: UUID, request: Request, turn: int = Query(ge=1, le=21)):
        form = await verified_form(request, call_id.hex)
        heard = str(form.get("SpeechResult", ""))[:2000]
        return xml_response(await run_in_threadpool(controller.conversation, call_id.hex, turn, heard))

    @webhooks.post("/status/{call_id}")
    async def status(call_id: UUID, request: Request):
        form = await verified_form(request, call_id.hex)
        sequence = form.get("SequenceNumber", "")
        controller.contexts.status(call_id.hex, form.get("CallStatus", ""), int(sequence) if sequence.isdigit() else None)
        return Response(status_code=204)

    @webhooks.post("/fallback/{call_id}")
    async def fallback(call_id: UUID, request: Request):
        await verified_form(request, call_id.hex)
        return xml_response(controller.twiml(call_id.hex, 0, "I'm sorry, WTD's automated assistant is unavailable. Thank you and goodbye.", True))

    app.include_router(api)
    app.include_router(webhooks)

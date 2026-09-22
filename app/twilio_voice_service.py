"""Twilio transport: only a reserved, approved call may invoke this service."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import phonenumbers
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from app.store import StoreError


def normalise_phone(value: str) -> str:
    value = str(value or "").strip()
    if not value.startswith("+"):
        raise StoreError(422, "Use the international phone format, for example +442034110180.")
    try:
        number = phonenumbers.parse(value, None)
        if number.extension or not phonenumbers.is_valid_number(number):
            raise ValueError
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise StoreError(422, "Enter a valid international phone number without an extension.") from None


@dataclass(frozen=True)
class VoiceSettings:
    account_sid: str = ""
    auth_token: str = ""
    phone_number: str = ""
    public_base_url: str = ""
    voice: str = "Polly.Amy"
    daily_limit: int = 20
    max_seconds: int = 300
    max_turns: int = 12

    @classmethod
    def from_env(cls):
        def env(key, default=""):
            return os.getenv(key, default).strip()
        return cls(env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"), env("TWILIO_PHONE_NUMBER"),
                   env("PUBLIC_BASE_URL").rstrip("/"), env("TWILIO_VOICE", "Polly.Amy"),
                   int(env("VOICE_DAILY_LIMIT", "20")), int(env("VOICE_MAX_SECONDS", "300")),
                   int(env("VOICE_MAX_TURNS", "12")))

    def validate(self):
        url = urlparse(self.public_base_url)
        if not re.fullmatch(r"AC[0-9a-fA-F]{32}", self.account_sid) or not self.auth_token:
            raise StoreError(503, "Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in the backend .env.")
        if (url.scheme != "https" or not url.hostname or url.path not in ("", "/")
                or url.query or url.fragment or url.username or url.port not in (None, 443)):
            raise StoreError(503, "PUBLIC_BASE_URL must be the public HTTPS origin that reaches FastAPI, without a path.")
        normalise_phone(self.phone_number)
        if self.voice not in ("Polly.Amy", "Polly.Brian", "Polly.Emma"):
            raise StoreError(503, "Choose a supported British voice: Polly.Amy, Polly.Brian or Polly.Emma.")
        if not (0 <= self.daily_limit <= 100 and 30 <= self.max_seconds <= 600 and 2 <= self.max_turns <= 20):
            raise StoreError(503, "Use VOICE_DAILY_LIMIT 0–100, VOICE_MAX_SECONDS 30–600 and VOICE_MAX_TURNS 2–20.")

    def url(self, action: str, call_id: str, turn: int | None = None) -> str:
        call_id = UUID(call_id).hex
        suffix = f"?turn={turn}" if turn is not None else ""
        return f"{self.public_base_url}/voice/{action}/{call_id}{suffix}"


class TwilioVoiceService:
    def __init__(self, settings: VoiceSettings | None = None):
        self.settings = settings or VoiceSettings.from_env()
        self.settings.validate()
        self.client = Client(self.settings.account_sid, self.settings.auth_token,
                             http_client=TwilioHttpClient(timeout=8, max_retries=0))

    def make_outbound_call(self, destination_number: str, call_id: str):
        s = self.settings
        return self.client.calls.create(
            to=normalise_phone(destination_number), from_=normalise_phone(s.phone_number),
            url=s.url("outbound-answer", call_id), method="POST",
            fallback_url=s.url("fallback", call_id), fallback_method="POST",
            status_callback=s.url("status", call_id), status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            timeout=25, time_limit=s.max_seconds, record=False,
        )

    def fetch_call(self, sid: str):
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", sid):
            raise StoreError(422, "Invalid Twilio Call SID.")
        return self.client.calls(sid).fetch()

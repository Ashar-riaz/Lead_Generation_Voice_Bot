"""Saved contact corrections and explicit, credit-using ZoomInfo company lookup."""
from __future__ import annotations

import json
import threading
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import Field

from app.responses import LeadView
from app.schemas import RequestModel
from app.store import StoreError
from app.twilio_voice_service import normalise_phone
from src.models import Contact
from src.zoominfo.auth import ZoomInfoAuthError
from src.zoominfo.client import ZoomInfoClient, ZoomInfoError


class ContactEdit(RequestModel):
    expected_version: int = Field(ge=1)
    company_phone: str = Field(default="", max_length=80)
    person_id: str | None = Field(default=None, max_length=120)
    contact_name: str = Field(default="", max_length=120)
    contact_phone: str = Field(default="", max_length=80)


class ContactVersion(RequestModel):
    expected_version: int = Field(ge=1)


class ContactController:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.provider_factory = lambda: ZoomInfoClient(settings, max_retries=0)
        self.locks = [threading.Lock() for _ in range(32)]

    def update(self, lead_id, version, transform):
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT payload FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                raise StoreError(404, "Lead not found.")
            value = json.loads(row[0])
            if value.get("contact_version", 1) != version:
                raise StoreError(409, "Contact details changed in another window. Reload them before saving.")
            transform(value)
            value["contact_version"] = version + 1
            db.execute("UPDATE leads SET payload=? WHERE id=?", (json.dumps(value), lead_id))
            # Existing plans are snapshots. Require a fresh plan using the corrected data.
            db.execute("""UPDATE voice_calls SET ready_to_call=0,approved_version=NULL,status='superseded',
                          version=version+1,last_error='Contact details changed. Save and approve a new call plan.'
                          WHERE lead_id=? AND attempted_at IS NULL AND status IN ('draft','ready')""", (lead_id,))
        return self.store.lead(lead_id)

    def edit(self, lead_id, payload):
        def apply(value):
            # Unchanged provider strings may be stored as supplied; corrections require +country code.
            phone = payload.company_phone.strip()
            if phone != value.get("company_phone", ""):
                value["company_phone"] = normalise_phone(phone) if phone else ""
                value["company_phone_source"] = "manual"
            contacts = value.setdefault("contacts", [])
            target = next((c for c in contacts if c.get("person_id") == payload.person_id), None) if payload.person_id else None
            if payload.person_id and target is None:
                raise StoreError(404, "Contact no longer exists. Reload the company details.")
            if target is None and (payload.contact_name.strip() or payload.contact_phone.strip()):
                if len(contacts) >= 30:
                    raise StoreError(422, "A company can have at most 30 saved contacts.")
                if not payload.contact_name.strip():
                    raise StoreError(422, "Enter a name for the new contact.")
                from dataclasses import asdict
                target = asdict(Contact(person_id="manual-" + uuid4().hex))
                contacts.append(target)
            if target is not None:
                name = payload.contact_name.strip()
                if name != f'{target.get("first_name", "")} {target.get("last_name", "")}'.strip():
                    target.update(first_name=name, last_name="", name_source="manual")
                number = payload.contact_phone.strip()
                if number != target.get("phone", ""):
                    target.update(phone=normalise_phone(number) if number else "", phone_source="manual")
        return self.update(lead_id, payload.expected_version, apply)

    def lookup(self, lead_id, version):
        with self.locks[sum(lead_id.encode()) % len(self.locks)]:
            lead = self.store.lead(lead_id)
            if lead.get("contact_version", 1) != version:
                raise StoreError(409, "Contact details changed. Reload before using ZoomInfo credits.")
            if self.store.run(lead["run_id"])["mock"]:
                raise StoreError(409, "Sample leads cannot use live ZoomInfo enrichment.")
            if not self.settings.has_zoominfo:
                raise StoreError(503, "Configure ZoomInfo client credentials before fetching a company number.")
            try:
                number = self.provider_factory().company_phone(lead["company_id"])
            except (ZoomInfoError, ZoomInfoAuthError) as exc:
                raise StoreError(502, str(exc)) from None
            def apply(value):
                value["zoominfo_company_phone"] = number
                if value.get("company_phone_source") != "manual":
                    value.update(company_phone=number, company_phone_source="zoominfo" if number else "")
            updated = self.update(lead_id, version, apply)
            return {"lead": updated, "message": ("Company phone returned by ZoomInfo. Saved manual corrections are preserved."
                    if number else "ZoomInfo returned no company phone. Your subscription or this record may not provide one; no number was invented.")}


def install_contacts(app, settings, store, authenticate):
    controller = ContactController(store, settings)
    app.state.contacts = controller
    api = APIRouter(prefix="/api/v1/leads", tags=["Contact details"], dependencies=[Depends(authenticate)])

    @api.patch("/{lead_id}/contact", response_model=LeadView)
    def edit(lead_id: str, payload: ContactEdit):
        return controller.edit(lead_id, payload)

    @api.post("/{lead_id}/company-phone")
    def company_phone(lead_id: str, payload: ContactVersion):
        return controller.lookup(lead_id, payload.expected_version)

    app.include_router(api)
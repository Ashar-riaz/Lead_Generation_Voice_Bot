"""Offline stand-in for ZoomInfoClient. Uses data/mock/*.json (fictional companies).

Run with --mock to test the full pipeline before you have ZoomInfo credentials.
"""
from __future__ import annotations

import json

from config.settings import Settings
from src.models import Contact, IntentSignal, Lead


class MockZoomInfoClient:
    def __init__(self, settings: Settings):
        self.data = json.loads((settings.mock_dir / "intent_results.json").read_text())

    def intent_topics(self) -> list[str]:
        return self.data["intent_topics"]

    def lookup(self, field_name: str, use_cache: bool = True) -> list[dict]:
        if field_name == "intent-topics":
            return [{"id": t, "attributes": {"name": t}} for t in self.data["intent_topics"]]
        return []

    def search_intent(self, topics, country="", signal_score_min=60, extra_filters=None, **_) -> list[Lead]:
        filters = extra_filters or {}
        if any(filters.get(k) for k in ("state", "metroRegion", "industryCodes", "employeeCount", "revenue", "techAttributeTagList")):
            raise ValueError("Company and state/metro filters require live ZoomInfo data")
        # The bundled fictional examples are UK companies only.
        if country and country.casefold() not in ("united kingdom", "uk", "gb", "great britain"):
            return []
        wanted = {t.lower() for t in topics}
        leads = []
        for c in self.data["companies"]:
            signals = [
                IntentSignal(**s) for s in c["signals"]
                if s["topic"].lower() in wanted and signal_score_min <= s["signal_score"] <= filters.get("signalScoreMax", 100)
                and (not filters.get("signalStartDate") or s["signal_date"][:10] >= filters["signalStartDate"])
                and (not filters.get("signalEndDate") or s["signal_date"][:10] <= filters["signalEndDate"])
                and (not filters.get("audienceStrengthMin") or s["audience_strength"] <= filters["audienceStrengthMin"])
                and (not filters.get("audienceStrengthMax") or s["audience_strength"] >= filters["audienceStrengthMax"])
            ]
            if not signals:
                continue
            leads.append(
                Lead(
                    company_id=c["company_id"],
                    company_name=c["company_name"],
                    website=c["website"],
                    signals=signals,
                    contacts=[Contact(**rc) for rc in c.get("recommended_contacts", [])],
                )
            )
        return leads

    def search_contacts(self, company_id: str, job_titles: list[str], page_size: int = 10) -> list[Contact]:
        for c in self.data["companies"]:
            if c["company_id"] == company_id:
                return [Contact(**{k: v for k, v in b.items() if k not in ("email", "phone")}) for b in c.get("buyers", [])]
        return []

    def enrich_contacts(self, contacts: list[Contact]) -> list[Contact]:
        index = {}
        for c in self.data["companies"]:
            for p in c.get("buyers", []) + c.get("recommended_contacts", []):
                index[p["person_id"]] = p
        for contact in contacts:
            src = index.get(contact.person_id, {})
            contact.email = src.get("email", contact.email)
            contact.phone = src.get("phone", contact.phone)
        return contacts

"""ZoomInfo GTM Data API client.

Endpoints used (base https://api.zoominfo.com/gtm):
  GET  /data/v1/lookup/{fieldName}   valid filter values (intent-topics, countries, ...)
  POST /data/v1/intent/search        companies researching given intent topics
  POST /data/v1/contacts/search      find decision makers at a company (free, no emails)
  POST /data/v1/contacts/enrich      get emails/phones (uses credits)
"""
from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import time
from typing import Any
from uuid import uuid4

import requests

from config.settings import Settings
from src.models import Contact, IntentSignal, Lead
from src.zoominfo.auth import shared_token_manager
from src.zoominfo.filters import LOOKUP_FIELDS

log = logging.getLogger(__name__)

JSONAPI = "application/vnd.api+json"
LOOKUP_CACHE_TTL = 7 * 24 * 3600  # lookups rarely change


class ZoomInfoError(RuntimeError):
    pass


class ZoomInfoClient:
    def __init__(self, settings: Settings, max_retries: int = 4):
        self.settings = settings
        self.base_url = settings.zoominfo_base_url.rstrip("/")
        self.cache_dir = settings.cache_dir
        self.max_retries = max_retries
        self.tokens = shared_token_manager(
            settings.zoominfo_client_id,
            settings.zoominfo_client_secret,
            settings.zoominfo_token_url,
            settings.zoominfo_scope,
        )
        self.session = requests.Session()

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, *, params=None, body=None) -> dict:
        url = f"{self.base_url}{path}"
        attempt, renewed = 0, False
        while True:
            token = self.tokens.get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": JSONAPI,
                "Content-Type": JSONAPI,
            }
            try:
                resp = self.session.request(method, url, params=params, json=body, headers=headers, timeout=60)
            except requests.RequestException:
                raise ZoomInfoError("Could not reach ZoomInfo. Check connectivity and try again.") from None

            if resp.status_code == 401 and not renewed:
                self.tokens.invalidate(token)
                renewed = True
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise ZoomInfoError(f"ZoomInfo is busy or rate limited (HTTP {resp.status_code}). Try again later.")
                try:
                    wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                if not math.isfinite(wait) or wait > 30:
                    raise ZoomInfoError("ZoomInfo requested a longer rate-limit wait. Try the search again later.")
                wait = max(0, wait)
                log.warning("ZoomInfo %s on %s, retrying in %.1fs", resp.status_code, path, wait)
                time.sleep(wait)
                attempt += 1
                continue
            if resp.status_code == 401:
                raise ZoomInfoError("ZoomInfo rejected the renewed access token. Check the app credentials and integration user.")
            if resp.status_code == 403:
                raise ZoomInfoError("ZoomInfo denied access. Check your app scopes and subscription entitlement for this endpoint.")
            if resp.status_code >= 400:
                raise ZoomInfoError(f"ZoomInfo rejected {path} (HTTP {resp.status_code}). Check the selected filters against ZoomInfo lookup values.")
            try:
                result = resp.json() if resp.content else {}
                if not isinstance(result, dict):
                    raise ValueError
                return result
            except ValueError:
                raise ZoomInfoError("ZoomInfo returned an invalid API response. Try again later.") from None

    # ---------------------------------------------------------------- lookup
    def lookup(self, field_name: str, use_cache: bool = True) -> list[dict]:
        """Valid values for a filter, e.g. 'intent-topics', 'countries', 'management-levels'."""
        if field_name not in LOOKUP_FIELDS:
            raise ValueError("Unsupported ZoomInfo lookup field")
        # Topic entitlements can differ by account/app scope. Never share their cache.
        account = hashlib.sha256(f"{self.base_url}|{self.settings.zoominfo_client_id}|{self.settings.zoominfo_scope}".encode()).hexdigest()[:16]
        cache_file = self.cache_dir / account / f"lookup_{field_name}.json"
        if use_cache and cache_file.exists() and time.time() - cache_file.stat().st_mtime < LOOKUP_CACHE_TTL:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, list):
                    return cached
            except (ValueError, OSError):
                pass

        data = self._request("GET", f"/data/v1/lookup/{field_name}").get("data", [])
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, list):
            raise ZoomInfoError("ZoomInfo returned an invalid lookup response")
        temp = cache_file.with_name(f".{uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temp, cache_file)
        finally:
            temp.unlink(missing_ok=True)
        return data

    def intent_topics(self) -> list[str]:
        return [row["attributes"]["name"] for row in self.lookup("intent-topics")]

    # ---------------------------------------------------------------- intent
    def search_intent(
        self,
        topics: list[str],
        country: str = "",
        signal_score_min: int = 60,
        page_size: int = 50,
        max_pages: int = 2,
        extra_filters: dict[str, Any] | None = None,
    ) -> list[Lead]:
        if not 1 <= len(topics) <= 50:
            raise ValueError("Between 1 and 50 intent topics are required")
        if not 60 <= signal_score_min <= 100:
            raise ValueError("Signal score must be between 60 and 100")

        attributes: dict[str, Any] = {
            "topics": topics,
            "signalScoreMin": signal_score_min,
            "signalScoreMax": 100,
            "findRecommendedContacts": True,
        }
        if country:
            attributes["country"] = country
        if extra_filters:
            allowed = {"signalScoreMin", "signalScoreMax", "signalStartDate", "signalEndDate",
                       "audienceStrengthMin", "audienceStrengthMax", "state", "metroRegion",
                       "industryCodes", "employeeCount", "revenue", "techAttributeTagList"}
            if set(extra_filters) - allowed:
                raise ValueError("Unsupported Intent Search filter")
            attributes.update(extra_filters)

        body = {"data": {"type": "IntentSearch", "attributes": attributes}}
        leads: dict[str, Lead] = {}

        for page in range(1, max_pages + 1):
            params = {"page[number]": page, "page[size]": page_size, "sort": "-signalScore"}
            payload = self._request("POST", "/data/v1/intent/search", params=params, body=body)
            rows = payload.get("data", [])
            if not isinstance(rows, list):
                raise ZoomInfoError("ZoomInfo returned an invalid intent response")
            for row in rows:
                self._merge_intent_row(leads, row.get("attributes", {}))

            links = payload.get("links") or {}
            if not rows or ("next" in links and not links["next"]) or ("next" not in links and len(rows) < page_size):
                break

        return list(leads.values())

    @staticmethod
    def _merge_intent_row(leads: dict[str, Lead], attrs: dict) -> None:
        company = attrs.get("company") or {}
        company_id = str(company.get("id") or "")
        if not company_id:
            return

        lead = leads.setdefault(
            company_id,
            Lead(company_id=company_id, company_name=company.get("name", ""), website=company.get("website", "")),
        )
        lead.signals.append(
            IntentSignal(
                topic=attrs.get("topic", ""),
                category=attrs.get("category", ""),
                signal_score=int(attrs.get("signalScore") or 0),
                audience_strength=attrs.get("audienceStrength", ""),
                signal_date=(attrs.get("signalDate") or "")[:10],
            )
        )
        known = {c.person_id for c in lead.contacts}
        for rc in attrs.get("recommendedContacts") or []:
            if str(rc.get("id")) not in known:
                lead.contacts.append(
                    Contact(
                        person_id=str(rc.get("id")),
                        first_name=rc.get("firstName", ""),
                        last_name=rc.get("lastName", ""),
                        job_title=rc.get("jobTitle", ""),
                    )
                )

    # -------------------------------------------------------------- contacts
    def search_contacts(self, company_id: str, job_titles: list[str], page_size: int = 10) -> list[Contact]:
        """Find buyers (HR / L&D / department heads) at a company. Free call, no emails returned."""
        attributes = {
            "companyId": str(company_id),
            "jobTitle": " OR ".join(job_titles),
            "requiredFields": "email",
            "contactAccuracyScoreMin": "80",
        }
        body = {"data": {"type": "ContactSearch", "attributes": attributes}}
        params = {"page[number]": 1, "page[size]": page_size, "sort": "-contactAccuracyScore"}
        payload = self._request("POST", "/data/v1/contacts/search", params=params, body=body)

        contacts = []
        for row in payload.get("data", []):
            a = row.get("attributes", {})
            contacts.append(
                Contact(
                    person_id=str(row.get("id")),
                    first_name=a.get("firstName", ""),
                    last_name=a.get("lastName", ""),
                    job_title=a.get("jobTitle", ""),
                    management_level=a.get("managementLevel", "") or "",
                    accuracy_score=a.get("contactAccuracyScore"),
                )
            )
        return contacts

    def enrich_contacts(self, contacts: list[Contact]) -> list[Contact]:
        """Adds email/phone. Each matched record costs a credit unless already under management."""
        by_id = {c.person_id: c for c in contacts if c.person_id}
        ids = list(by_id)
        for i in range(0, len(ids), 25):
            batch = ids[i : i + 25]
            body = {
                "data": {
                    "type": "ContactEnrich",
                    "attributes": {
                        "matchPersonInput": [{"personId": int(pid)} for pid in batch],
                        "outputFields": ["id", "firstName", "lastName", "email", "phone", "jobTitle", "managementLevel"],
                    },
                }
            }
            payload = self._request("POST", "/data/v1/contacts/enrich", body=body)
            for row in payload.get("data", []):
                if row.get("type") != "Contact":
                    continue
                pid = str((row.get("meta") or {}).get("input", {}).get("personId", row.get("id")))
                target = by_id.get(pid)
                if not target:
                    continue
                a = row.get("attributes", {})
                target.email = a.get("email") or target.email
                target.phone = a.get("phone") or target.phone
                target.job_title = a.get("jobTitle") or target.job_title
                levels = a.get("managementLevel") or []
                target.management_level = ", ".join(levels) if isinstance(levels, list) else str(levels)
        return contacts

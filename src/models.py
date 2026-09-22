"""Plain data models shared across the pipeline.

Graph state stores these as plain dicts (to_dict / from_dict) so LangGraph
checkpointers can serialise them safely.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Contact:
    person_id: str
    first_name: str = ""
    last_name: str = ""
    job_title: str = ""
    email: str = ""
    phone: str = ""
    management_level: str = ""
    accuracy_score: Optional[float] = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class IntentSignal:
    topic: str
    category: str = ""
    signal_score: int = 0
    audience_strength: str = ""
    signal_date: str = ""


@dataclass
class Lead:
    company_id: str
    company_name: str
    website: str = ""
    signals: list[IntentSignal] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    score: int = 0
    tier: str = ""
    score_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lead":
        d = dict(d)
        d["signals"] = [IntentSignal(**s) for s in d.get("signals", [])]
        d["contacts"] = [Contact(**c) for c in d.get("contacts", [])]
        return cls(**d)

    @property
    def top_signal(self) -> Optional[IntentSignal]:
        return max(self.signals, key=lambda s: s.signal_score, default=None)

    @property
    def primary_contact(self) -> Optional[Contact]:
        with_email = [c for c in self.contacts if c.email]
        return with_email[0] if with_email else (self.contacts[0] if self.contacts else None)


@dataclass
class SearchPlan:
    """What the query agent decided to search for."""
    raw_query: str
    category_key: str
    category_label: str
    intent_topics: list[str]
    buyer_titles: list[str]
    country: str = ""
    min_signal_score: int = 60
    intent_filters: dict = field(default_factory=dict)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SearchPlan":
        return cls(**d)


@dataclass
class EmailDraft:
    company_name: str
    to_name: str
    to_email: str
    subject: str
    body: str
    programme: str = ""
    company_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EmailDraft":
        return cls(**d)

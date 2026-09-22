"""LangGraph state + runtime dependencies.

State holds only JSON-friendly data (dicts, lists, strings) so it can be checkpointed.
Clients, the LLM and agents are passed separately as runtime context (LeadGenDeps).
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Optional, TypedDict

from config.settings import Settings, settings as default_settings
from src.agents.email_agent import EmailAgent
from src.agents.query_agent import QueryAgent
from src.knowledge.loader import KnowledgeBase, load_knowledge
from src.llm import LLMClient


class RunOptions(TypedDict, total=False):
    intent_topics: list[str]
    min_signal_score: Optional[int]
    max_signal_score: int
    signal_start_date: Optional[str]
    signal_end_date: Optional[str]
    audience_strength_min: Optional[str]
    audience_strength_max: Optional[str]
    state: str
    metro_region: str
    industry_codes: str
    employee_count: str
    revenue: str
    tech_products: str
    limit: int
    country: Optional[str]
    find_buyers: bool
    enrich: bool
    write_emails: bool
    send: bool
    min_tier: str
    contacts_per_lead: int


DEFAULT_OPTIONS: RunOptions = {
    "limit": 10,
    "country": None,
    "find_buyers": True,
    "enrich": True,
    "write_emails": True,
    "send": False,
    "min_tier": "Cool",
    "contacts_per_lead": 1,
}


class LeadGenState(TypedDict, total=False):
    # input
    query: str
    options: RunOptions
    # working data
    plan: dict
    leads: list[dict]
    drafts: list[dict]
    run_dir: str
    # review + sending
    review: dict            # {"approved": bool, "exclude": [emails]}
    sent: list[str]
    # append-only logs (reducers merge updates from every node)
    progress: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


@dataclass
class LeadGenDeps:
    settings: Settings
    zoominfo: Any                 # ZoomInfoClient or MockZoomInfoClient
    kb: KnowledgeBase
    llm: LLMClient
    query_agent: QueryAgent
    email_agent: EmailAgent
    mock: bool = False

    @classmethod
    def build(cls, settings: Settings, zoominfo, llm: LLMClient | None = None, mock: bool = False) -> "LeadGenDeps":
        kb = load_knowledge(settings.knowledge_dir)
        llm = llm or LLMClient(settings)
        return cls(
            settings=settings,
            zoominfo=zoominfo,
            kb=kb,
            llm=llm,
            query_agent=QueryAgent(kb, llm),
            email_agent=EmailAgent(kb, llm, settings),
            mock=mock,
        )

    @classmethod
    def from_env(cls) -> "LeadGenDeps":
        """Default to live data. Offline demos must explicitly supply mock dependencies."""
        from src.zoominfo.client import ZoomInfoClient
        return cls.build(default_settings, ZoomInfoClient(default_settings))

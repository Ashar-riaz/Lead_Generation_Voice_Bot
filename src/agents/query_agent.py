"""Query agent: "show me companies that need AI training" -> SearchPlan.

1. Pick the WTD training category the user means (LLM, or keyword fallback).
2. Pick matching ZoomInfo intent topics from the REAL topic list (lookup endpoint),
   so we never send topic names ZoomInfo doesn't recognise.
"""
from __future__ import annotations

import logging
import re

from src.knowledge.loader import KnowledgeBase
from src.llm import LLMClient, LLMUnavailable
from src.models import SearchPlan

log = logging.getLogger(__name__)

SYSTEM = """You map a sales rep's lead search request to a training category and ZoomInfo intent topics
for Workforce Training & Development (WTD), a UK apprenticeship training provider.
Only choose intent topics that appear EXACTLY in the provided topic list."""

PROMPT = """Request: "{query}"

WTD training categories:
{catalog}

Available ZoomInfo intent topics (choose from these only):
{topics}

Return JSON:
{{
  "category_key": "<one key from the categories list>",
  "intent_topics": ["<3 to 12 exact topics from the list, most relevant first>"],
  "country": "<country if the request names one, else empty string>",
  "min_signal_score": <60-100, use 70 unless the user asks for hot/strong leads (85)>,
  "reasoning": "<one sentence>"
}}"""


class QueryAgent:
    def __init__(self, kb: KnowledgeBase, llm: LLMClient):
        self.kb = kb
        self.llm = llm

    def plan(self, query: str, available_topics: list[str], default_country: str = "", intent_topics: list[str] | None = None) -> SearchPlan:
        result = None
        if intent_topics:
            canonical = {topic.casefold(): topic for topic in available_topics}
            if any(topic.casefold() not in canonical for topic in intent_topics):
                raise ValueError("Some selected topics are unavailable. Reload the ZoomInfo intent topic list and select exact names.")
            result = self._plan_with_keywords(query, available_topics)
            result["intent_topics"] = [canonical[topic.casefold()] for topic in intent_topics]
            result["reasoning"] = "Intent topics explicitly selected by the user from ZoomInfo lookup values."
        if result is None and self.llm.available:
            try:
                result = self._plan_with_llm(query, available_topics)
            except (LLMUnavailable, ValueError, KeyError) as exc:
                log.warning("LLM planning failed, using keyword fallback: %s", exc)
        if result is None:
            result = self._plan_with_keywords(query, available_topics)

        key = result["category_key"] if result["category_key"] in self.kb.categories else "ai"
        category = self.kb.category(key)
        topic_set = {t.lower(): t for t in available_topics}
        topics = [topic_set[t.lower()] for t in result.get("intent_topics", []) if t.lower() in topic_set]

        if not topics:
            raise ValueError(
                "No matching ZoomInfo intent topics found. Run `python main.py --topics <keyword>` "
                "to see which topics your subscription includes."
            )

        return SearchPlan(
            raw_query=query,
            category_key=key,
            category_label=category["label"],
            intent_topics=topics,
            buyer_titles=category["buyer_titles"],
            country=result.get("country") or default_country,
            min_signal_score=int(result.get("min_signal_score") or 70),
            reasoning=result.get("reasoning", ""),
        )

    # ------------------------------------------------------------------
    def _plan_with_llm(self, query: str, topics: list[str]) -> dict:
        prompt = PROMPT.format(
            query=query,
            catalog=self.kb.catalog_summary(),
            topics="\n".join(f"- {t}" for t in topics[:1500]),
        )
        return self.llm.generate_json(SYSTEM, prompt, temperature=0.1)

    def _plan_with_keywords(self, query: str, topics: list[str]) -> dict:
        q = query.lower()
        best_key, best_count = "ai", 0
        for key, cat in self.kb.categories.items():
            count = sum(1 for kw in cat["keywords"] if re.search(rf"\b{re.escape(kw)}\b", q))
            if count > best_count:
                best_key, best_count = key, count

        def hits(topic: str, kws: list[str]) -> bool:
            return any(re.search(rf"\b{re.escape(kw)}\b", topic.lower()) for kw in kws)

        keywords = self.kb.category(best_key)["keywords"]
        other_keywords = [kw for k, c in self.kb.categories.items() if k != best_key for kw in c["keywords"]]
        matched = [t for t in topics if hits(t, keywords)]
        # Generic training-buying topics help every category, but not ones owned by another category
        # (e.g. "AI Training" must not leak into a cyber search)
        matched += [
            t for t in topics
            if re.search(r"\b(training|apprenticeships?|upskilling|l&d|learning management)\b", t.lower())
            and not hits(t, other_keywords)
        ]

        hot = bool(re.search(r"\b(hot|urgent|strong|high intent)\b", q))
        return {
            "category_key": best_key,
            "intent_topics": list(dict.fromkeys(matched))[:12],
            "country": "",
            "min_signal_score": 85 if hot else 70,
            "reasoning": f"Keyword match on category '{best_key}'",
        }

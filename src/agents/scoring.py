"""Transparent, rule-based lead scoring (0-100). Easy to tweak, no LLM needed."""
from __future__ import annotations

import re
from datetime import date, datetime

from src.models import Lead, SearchPlan

AUDIENCE_POINTS = {"A": 20, "B": 16, "C": 11, "D": 6, "E": 3}
BUYER_PATTERN = re.compile(
    r"\b(learning|development|l&d|talent|people|hr|human resources|training|early careers|"
    r"head|director|chief|vp|coo|cto|cio|ciso)\b",
    re.IGNORECASE,
)


def score_lead(lead: Lead, plan: SearchPlan, today: date | None = None) -> Lead:
    today = today or date.today()
    points, reasons = 0, []

    top = lead.top_signal
    if top:
        # Signal strength: 60 -> 0 pts, 100 -> 40 pts
        s = max(0, min(40, round((top.signal_score - 60) * 1.0)))
        points += s
        reasons.append(f"signal {top.signal_score} on '{top.topic}' (+{s})")

        a = AUDIENCE_POINTS.get(top.audience_strength.upper(), 0)
        points += a
        if a:
            reasons.append(f"audience strength {top.audience_strength} (+{a})")

        # Recency
        try:
            days = (today - datetime.fromisoformat(top.signal_date).date()).days
            r = 10 if days <= 14 else 6 if days <= 30 else 2 if days <= 60 else 0
            points += r
            if r:
                reasons.append(f"signal {days}d ago (+{r})")
        except ValueError:
            pass

    # Several relevant topics = broader need
    if len(lead.signals) > 1:
        breadth = min(10, 5 * (len(lead.signals) - 1))
        points += breadth
        reasons.append(f"{len(lead.signals)} matching topics (+{breadth})")

    contact = lead.primary_contact
    if contact and contact.email:
        points += 10
        reasons.append("email available (+10)")
    if contact and BUYER_PATTERN.search(contact.job_title or ""):
        points += 10
        reasons.append(f"buyer title '{contact.job_title}' (+10)")

    lead.score = min(100, points)
    lead.tier = "Hot" if lead.score >= 70 else "Warm" if lead.score >= 45 else "Cool"
    lead.score_reasons = reasons
    return lead

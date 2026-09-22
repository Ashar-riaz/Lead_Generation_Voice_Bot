"""Email agent: writes a personalised subject + body for each lead using WTD's knowledge base."""
from __future__ import annotations

import logging
import re

from config.settings import Settings
from src.knowledge.loader import KnowledgeBase
from src.llm import LLMClient, LLMUnavailable
from src.models import EmailDraft, Lead, SearchPlan

log = logging.getLogger(__name__)

SENIOR = re.compile(r"\b(chief|director|head|vp|coo|ceo|cto|cio|founder|partner|owner)\b", re.I)

SYSTEM = """You write cold B2B outreach emails for Workforce Training & Development (WTD), a UK apprenticeship
training provider. You write like a thoughtful human account manager, not a marketer.

HARD RULES
- Use ONLY facts from the COMPANY KNOWLEDGE and PROGRAMME sections. Never invent numbers, clients, results or dates.
- Testimonials may only be quoted word for word from the knowledge base, or not used at all.
- Never mention ZoomInfo, intent data, tracking, or that you know what they have been researching.
  Frame relevance as a common challenge for organisations like theirs.
- UK English. Plain, warm, specific. No emojis. No em dashes or en dashes. No exclamation marks.
- Avoid clichés: "I hope this email finds you well", "reach out", "touch base", "game-changer",
  "unlock", "leverage", "in today's fast-paced world", "revolutionise".
- Body 90 to 150 words, 3 to 4 short paragraphs, one clear ask (a 15-minute call with a free funding check).
- Subject line: 3 to 7 words, sentence case, no clickbait, may include the company name.
- Do not include the signature or the opt-out line; they are added automatically."""

PROMPT = """COMPANY KNOWLEDGE
{profile}

PROGRAMME TO PITCH
Name: {programme_name}
In plain words: {programme_pitch}
Facts: {programme_facts}
Link: {programme_url}

LEAD
Company: {company}
Contact: {contact_name} ({contact_title})
Training area they are likely exploring: {topic}
Category: {category}

Return JSON: {{"subject": "...", "body": "..."}}
Start the body with "Hi {first_name},"."""


class EmailAgent:
    def __init__(self, kb: KnowledgeBase, llm: LLMClient, settings: Settings):
        self.kb = kb
        self.llm = llm
        self.settings = settings

    def write(self, lead: Lead, plan: SearchPlan) -> EmailDraft | None:
        contact = lead.primary_contact
        if not contact:
            log.info("Skipping %s: no contact", lead.company_name)
            return None

        programme = self.pick_programme(plan.category_key, contact.job_title)
        topic = lead.top_signal.topic if lead.top_signal else plan.category_label
        first_name = contact.first_name or "there"

        draft = None
        if self.llm.available:
            try:
                draft = self.llm.generate_json(
                    SYSTEM,
                    PROMPT.format(
                        profile=self.kb.profile,
                        programme_name=programme["name"],
                        programme_pitch=programme.get("pitch", ""),
                        programme_facts=programme["facts"],
                        programme_url=programme["url"],
                        company=short_company(lead.company_name),
                        contact_name=contact.full_name,
                        contact_title=contact.job_title or "unknown",
                        topic=topic,
                        category=plan.category_label,
                        first_name=first_name,
                    ),
                )
            except (LLMUnavailable, ValueError, KeyError) as exc:
                log.warning("LLM email failed for %s, using template: %s", lead.company_name, exc)

        if not draft or not draft.get("subject") or not draft.get("body"):
            draft = self._template(lead, programme, first_name)

        body = tidy(draft["body"]) + "\n\n" + self.signature()
        return EmailDraft(
            company_name=lead.company_name,
            to_name=contact.full_name,
            to_email=contact.email,
            subject=tidy(draft["subject"]).rstrip("."),
            body=body,
            programme=programme["name"],
            company_id=lead.company_id,
        )

    # ------------------------------------------------------------------
    def pick_programme(self, category_key: str, job_title: str) -> dict:
        programmes = self.kb.category(category_key)["programmes"]
        if category_key == "ai" and len(programmes) > 1:
            # Senior buyers get the leadership unit, others the practitioner route
            return programmes[0] if SENIOR.search(job_title or "") else programmes[1]
        return programmes[0]

    def signature(self) -> str:
        s = self.settings
        return (
            f"Best regards,\n{s.sender_name}\n{s.sender_title}\n"
            f"Workforce Training & Development\n{s.sender_phone} | {s.sender_email}\nwww.wtd.org.uk\n\n"
            "If this isn't relevant, just reply \"no thanks\" and I won't contact you again."
        )

    def _template(self, lead: Lead, programme: dict, first_name: str) -> dict:
        """Used when no LLM is configured. Plain, human, fact-only."""
        company = short_company(lead.company_name)
        area = programme_area(programme["name"])
        return {
            "subject": f"Funded {area} training for {company}",
            "body": (
                f"Hi {first_name},\n\n"
                f"A lot of organisations like {company} want their people to build {area} skills "
                "but struggle to find the time and budget to do it properly.\n\n"
                f"One option worth a look is {display_name(programme['name'])}. It's {programme['pitch']}. "
                "Most of our programmes are 95 to 100% government-funded, our tutors come from the sectors "
                "they teach, and we handle the funding paperwork for you.\n\n"
                "Would a 15-minute call next week be useful? I can run a free funding check for your team "
                "while we talk.\n\n"
                f"Programme details: {programme['url']}"
            ),
        }

def clean_company(name: str) -> str:
    return re.sub(r"\s*\(SAMPLE\)\s*", "", name).strip()


def short_company(name: str) -> str:
    """'Northbridge Logistics Ltd' -> 'Northbridge Logistics' (reads more naturally in an email)."""
    name = clean_company(name)
    return re.sub(r",?\s+(ltd\.?|limited|plc|llp|inc\.?|group plc)$", "", name, flags=re.I).strip()


def display_name(programme_name: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", programme_name).strip()


def programme_area(programme_name: str) -> str:
    n = programme_name.lower()
    for needle, area in [("ai ", "AI"), ("cyber", "cyber security"), ("data", "data"), ("market", "marketing"),
                         ("customer", "customer service"), ("project", "project management"),
                         ("business analyst", "business analysis"), ("it ", "IT"), ("information", "IT"),
                         ("digital", "digital"), ("food", "food production"), ("butcher", "butchery"),
                         ("engineering", "engineering")]:
        if needle in n + " ":
            return area
    return "workforce"


def tidy(text: str) -> str:
    """Remove AI-ish punctuation the style guide bans, leaving quoted text (testimonials) untouched."""
    parts = re.split(r'("[^"]*"|“[^”]*”)', text)
    for i, part in enumerate(parts):
        if i % 2 == 1:  # quoted segment, keep verbatim
            continue
        part = re.sub(r"\s*[—–]\s*", ", ", part)
        part = re.sub(r"!(?=\s|$)", ".", part)
        part = re.sub(r",\s*,", ",", part)
        parts[i] = re.sub(r"[ \t]+\n", "\n", part)
    return "".join(parts).strip()

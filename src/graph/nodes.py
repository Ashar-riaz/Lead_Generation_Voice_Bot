"""Graph nodes. Each node reads state, does one job, and returns only the keys it changes."""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from src.agents.scoring import score_lead
from src.graph.state import DEFAULT_OPTIONS, LeadGenDeps, LeadGenState
from src.models import EmailDraft, Lead, SearchPlan
from src.output.exporter import export_emails, export_leads, make_run_dir
from src.zoominfo.filters import IntentFilters

log = logging.getLogger(__name__)
TIERS = ["Cool", "Warm", "Hot"]


# ---------------------------------------------------------------- helpers
@lru_cache(maxsize=1)
def _default_deps() -> LeadGenDeps:
    return LeadGenDeps.from_env()


def deps(runtime: Runtime[LeadGenDeps]) -> LeadGenDeps:
    return runtime.context or _default_deps()


def opts(state: LeadGenState) -> dict:
    return {**DEFAULT_OPTIONS, **(state.get("options") or {})}


def load_leads(state: LeadGenState) -> list[Lead]:
    return [Lead.from_dict(d) for d in state.get("leads", [])]


def dump_leads(leads: list[Lead]) -> list[dict]:
    return [l.to_dict() for l in leads]


def plan_of(state: LeadGenState) -> SearchPlan:
    return SearchPlan.from_dict(state["plan"])


# ------------------------------------------------------------------ nodes
def plan_search(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d, o = deps(runtime), opts(state)
    topics = d.zoominfo.intent_topics()
    filters = IntentFilters(**{key: o[key] for key in IntentFilters.model_fields if key in o})
    plan = d.query_agent.plan(state["query"], topics, default_country=d.settings.default_country,
                              intent_topics=filters.intent_topics)
    if o["country"] is not None:
        plan.country = o["country"]
    plan.min_signal_score = (filters.min_signal_score if filters.min_signal_score is not None
                             else min(max(60, plan.min_signal_score), filters.max_signal_score))
    plan.intent_filters = filters.zoominfo_attributes()
    plan.intent_filters["signalScoreMin"] = plan.min_signal_score
    return {
        "plan": plan.to_dict(),
        "progress": [f"plan_search: {plan.category_label} | {len(plan.intent_topics)} topics | country={plan.country or 'any'}"],
    }


def search_companies(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d, o, plan = deps(runtime), opts(state), plan_of(state)
    leads = d.zoominfo.search_intent(plan.intent_topics, country=plan.country, signal_score_min=plan.min_signal_score,
                                    extra_filters=plan.intent_filters)
    leads.sort(key=lambda l: l.top_signal.signal_score if l.top_signal else 0, reverse=True)
    leads = leads[: o["limit"] * 2]  # headroom before scoring
    return {"leads": dump_leads(leads), "progress": [f"search_companies: {len(leads)} companies with intent"]}


def find_buyers(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d, plan = deps(runtime), plan_of(state)
    leads, errors = load_leads(state), []
    for lead in leads:
        try:
            buyers = d.zoominfo.search_contacts(lead.company_id, plan.buyer_titles, page_size=3)
        except Exception as exc:  # one bad company should not stop the run
            errors.append(f"find_buyers[{lead.company_name}]: {exc}")
            continue
        known = {c.person_id for c in lead.contacts}
        lead.contacts = [b for b in buyers if b.person_id not in known] + lead.contacts
    found = sum(1 for l in leads if l.contacts)
    return {"leads": dump_leads(leads), "errors": errors,
            "progress": [f"find_buyers: contacts found at {found}/{len(leads)} companies"]}


def shortlist(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    """Score before enrichment so credits are only spent on the best leads."""
    o, plan = opts(state), plan_of(state)
    leads = load_leads(state)
    for lead in leads:
        lead.contacts = lead.contacts[: o["contacts_per_lead"]]
        score_lead(lead, plan)
    leads = sorted(leads, key=lambda l: l.score, reverse=True)[: o["limit"]]
    return {"leads": dump_leads(leads), "progress": [f"shortlist: kept top {len(leads)}"]}


def enrich_contacts(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d = deps(runtime)
    leads = load_leads(state)
    todo = [c for l in leads for c in l.contacts if not c.email]
    errors = []
    if todo:
        try:
            d.zoominfo.enrich_contacts(todo)  # mutates the Contact objects in place
        except Exception as exc:
            errors.append(f"enrich_contacts: {exc}")
    with_email = sum(1 for l in leads if l.primary_contact and l.primary_contact.email)
    return {"leads": dump_leads(leads), "errors": errors,
            "progress": [f"enrich_contacts: enriched {len(todo)} contacts, {with_email} leads now have an email"]}


def final_score(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    o, plan = opts(state), plan_of(state)
    leads = load_leads(state)
    for lead in leads:
        score_lead(lead, plan)
    floor = TIERS.index(o["min_tier"])
    leads = sorted((l for l in leads if TIERS.index(l.tier) >= floor), key=lambda l: l.score, reverse=True)
    counts = {t: sum(1 for l in leads if l.tier == t) for t in reversed(TIERS)}
    return {"leads": dump_leads(leads),
            "progress": [f"final_score: " + ", ".join(f"{v} {k}" for k, v in counts.items())]}


def write_emails(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d, plan = deps(runtime), plan_of(state)
    drafts, errors = [], []
    for lead in load_leads(state):
        try:
            draft = d.email_agent.write(lead, plan)
        except Exception as exc:
            errors.append(f"write_emails[{lead.company_name}]: {exc}")
            continue
        if draft and draft.to_email:
            drafts.append(draft.to_dict())
    return {"drafts": drafts, "errors": errors, "progress": [f"write_emails: {len(drafts)} drafts"]}


def export_results(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    d = deps(runtime)
    run_dir = make_run_dir(d.settings.output_dir, state["query"])
    plan = plan_of(state)
    export_leads(run_dir, load_leads(state), plan)
    drafts = [EmailDraft.from_dict(x) for x in state.get("drafts", [])]
    if drafts:
        export_emails(run_dir, drafts)
    return {"run_dir": str(run_dir), "progress": [f"export_results: saved to {run_dir}"]}


def human_review(state: LeadGenState) -> dict:
    """Pauses the graph until a person approves sending.

    Resume with Command(resume={"approved": True, "exclude": ["a@b.com"]}) or Command(resume=False).
    """
    drafts = state.get("drafts", [])
    decision = interrupt({
        "message": f"Review {len(drafts)} drafts before sending",
        "run_dir": state.get("run_dir"),
        "drafts": [{"to": d["to_email"], "company": d["company_name"], "subject": d["subject"]} for d in drafts],
    })
    if isinstance(decision, bool):
        decision = {"approved": decision}
    decision = {"approved": bool(decision.get("approved")), "exclude": list(decision.get("exclude", []))}
    return {"review": decision,
            "progress": [f"human_review: {'approved' if decision['approved'] else 'rejected'}"]}


def send_emails(state: LeadGenState, runtime: Runtime[LeadGenDeps]) -> dict:
    if state.get("review", {}).get("approved") is not True:
        return {"sent": [], "progress": ["send_emails: approval required, nothing sent"]}
    d = deps(runtime)
    exclude = {e.lower() for e in state.get("review", {}).get("exclude", [])}
    drafts = [EmailDraft.from_dict(x) for x in state.get("drafts", []) if x["to_email"].lower() not in exclude]

    if d.mock:
        return {"sent": [], "progress": [f"send_emails: mock mode, {len(drafts)} emails NOT sent"]}

    from src.output.sender import EmailSender
    try:
        sent = EmailSender(d.settings).send_all(drafts, approved=True)
    except Exception as exc:
        return {"sent": [], "errors": [f"send_emails: {exc}"], "progress": ["send_emails: failed"]}
    return {"sent": sent, "progress": [f"send_emails: sent {len(sent)}"]}

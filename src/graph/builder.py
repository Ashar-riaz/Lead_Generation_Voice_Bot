"""Builds the lead-generation StateGraph.

    START
      │
    plan_search
      │
    search_companies ──(no companies)──────────────────────┐
      │                                                    │
    [find_buyers]  (skipped with --no-buyers)              │
      │                                                    │
    shortlist                                              │
      │                                                    │
    [enrich_contacts]  (skipped with --no-enrich)          │
      │                                                    │
    final_score                                            │
      │                                                    │
    [write_emails]  (skipped with --no-emails)             │
      │                                                    │
    export_results ◄───────────────────────────────────────┘
      │
      ├──(send requested and drafts exist)── human_review ──(approved)── send_emails ── END
      │                                            └──(rejected)── END
      └── END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.graph import nodes
from src.graph.state import LeadGenDeps, LeadGenState


# ------------------------------------------------------------------ routers
def after_search(state: LeadGenState) -> str:
    if not state.get("leads"):
        return "export_results"
    return "find_buyers" if nodes.opts(state)["find_buyers"] else "shortlist"


def after_shortlist(state: LeadGenState) -> str:
    return "enrich_contacts" if nodes.opts(state)["enrich"] else "final_score"


def after_scoring(state: LeadGenState) -> str:
    if nodes.opts(state)["write_emails"] and state.get("leads"):
        return "write_emails"
    return "export_results"


def after_export(state: LeadGenState) -> str:
    return "human_review" if nodes.opts(state)["send"] and state.get("drafts") else END


def after_review(state: LeadGenState) -> str:
    return "send_emails" if state.get("review", {}).get("approved") else END


# ------------------------------------------------------------------ builder
def build_graph(checkpointer=None):
    g = StateGraph(LeadGenState, context_schema=LeadGenDeps)

    g.add_node("plan_search", nodes.plan_search)
    g.add_node("search_companies", nodes.search_companies)
    g.add_node("find_buyers", nodes.find_buyers)
    g.add_node("shortlist", nodes.shortlist)
    g.add_node("enrich_contacts", nodes.enrich_contacts)
    g.add_node("final_score", nodes.final_score)
    g.add_node("write_emails", nodes.write_emails)
    g.add_node("export_results", nodes.export_results)
    g.add_node("human_review", nodes.human_review)
    g.add_node("send_emails", nodes.send_emails)

    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "search_companies")
    g.add_conditional_edges("search_companies", after_search, ["find_buyers", "shortlist", "export_results"])
    g.add_edge("find_buyers", "shortlist")
    g.add_conditional_edges("shortlist", after_shortlist, ["enrich_contacts", "final_score"])
    g.add_edge("enrich_contacts", "final_score")
    g.add_conditional_edges("final_score", after_scoring, ["write_emails", "export_results"])
    g.add_edge("write_emails", "export_results")
    g.add_conditional_edges("export_results", after_export, ["human_review", END])
    g.add_conditional_edges("human_review", after_review, ["send_emails", END])
    g.add_edge("send_emails", END)

    # A checkpointer is required for human_review (interrupt/resume)
    return g.compile(checkpointer=checkpointer)


def initial_state(query: str, **options) -> LeadGenState:
    return {"query": query, "options": options, "progress": [], "errors": []}

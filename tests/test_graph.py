from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from config.settings import Settings
from src.graph.builder import build_graph, initial_state
from src.graph.state import LeadGenDeps
from src.zoominfo.mock_client import MockZoomInfoClient

QUERY = "companies that need AI training"


@pytest.fixture
def deps(tmp_path):
    s = Settings()
    s.llm_provider = "none"
    s.output_dir = tmp_path
    llm = MagicMock(available=False)
    return LeadGenDeps.build(s, MockZoomInfoClient(s), llm=llm, mock=True)


def nodes_run(graph, state_in, deps, config=None):
    visited = []
    for chunk in graph.stream(state_in, config, context=deps, stream_mode="updates"):
        visited += list(chunk)
    return visited


def test_full_path_visits_every_step(deps):
    visited = nodes_run(build_graph(), initial_state(QUERY), deps)
    assert visited == ["plan_search", "search_companies", "find_buyers", "shortlist",
                       "enrich_contacts", "final_score", "write_emails", "export_results"]


def test_optional_steps_are_skipped(deps):
    visited = nodes_run(build_graph(),
                        initial_state(QUERY, find_buyers=False, enrich=False, write_emails=False), deps)
    assert visited == ["plan_search", "search_companies", "shortlist", "final_score", "export_results"]


def test_no_results_goes_straight_to_export(deps):
    state = build_graph().invoke(initial_state("hot leads for marketing training"), context=deps)
    assert state["leads"] == []
    assert not any(p.startswith("find_buyers") for p in state["progress"])
    assert state["run_dir"]


def test_review_approve_with_exclusion(deps):
    graph = build_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}
    first = graph.invoke(initial_state(QUERY, send=True), cfg, context=deps)

    request = first["__interrupt__"][0].value
    assert len(request["drafts"]) == 2
    skip = request["drafts"][1]["to"]

    final = graph.invoke(Command(resume={"approved": True, "exclude": [skip]}), cfg, context=deps)
    assert final["review"] == {"approved": True, "exclude": [skip]}
    assert "send_emails: mock mode, 1 emails NOT sent" in final["progress"]


def test_review_reject_ends_without_sending(deps):
    graph = build_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t2"}}
    graph.invoke(initial_state(QUERY, send=True), cfg, context=deps)
    final = graph.invoke(Command(resume=False), cfg, context=deps)
    assert final["review"]["approved"] is False
    assert not any(p.startswith("send_emails") for p in final["progress"])


def test_real_send_uses_sender_and_respects_exclusions(deps, monkeypatch):
    deps.mock = False
    sender = MagicMock()
    sender.return_value.send_all.side_effect = lambda drafts, **kwargs: [d.to_email for d in drafts]
    monkeypatch.setattr("src.output.sender.EmailSender", sender)

    graph = build_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t3"}}
    first = graph.invoke(initial_state(QUERY, send=True), cfg, context=deps)
    skip = first["__interrupt__"][0].value["drafts"][0]["to"]
    final = graph.invoke(Command(resume={"approved": True, "exclude": [skip]}), cfg, context=deps)

    assert skip not in final["sent"] and len(final["sent"]) == 1
    assert sender.return_value.send_all.call_args.kwargs["approved"] is True


def test_node_errors_are_collected_not_fatal(deps):
    deps.zoominfo.search_contacts = MagicMock(side_effect=RuntimeError("rate limited"))
    state = build_graph().invoke(initial_state(QUERY), context=deps)
    assert any("rate limited" in e for e in state["errors"])
    assert state["leads"]  # run still completed using recommended contacts


def test_studio_graph_runs_without_context(monkeypatch, tmp_path):
    from src.graph import nodes
    from src.graph.studio import graph
    s = Settings()
    s.llm_provider = "none"
    s.output_dir = tmp_path
    monkeypatch.setattr(nodes, "_default_deps",
                        lambda: LeadGenDeps.build(s, MockZoomInfoClient(s), llm=MagicMock(available=False), mock=True))
    state = graph.invoke({"query": QUERY, "options": {"limit": 2}})
    assert len(state["leads"]) <= 2

"""Programmatic entry point. The actual flow lives in the LangGraph graph (src/graph)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config.settings import Settings
from src.graph.builder import build_graph, initial_state
from src.graph.state import LeadGenDeps
from src.llm import LLMClient
from src.models import EmailDraft, Lead, SearchPlan


@dataclass
class RunResult:
    plan: SearchPlan
    leads: list[Lead]
    drafts: list[EmailDraft] = field(default_factory=list)
    run_dir: Path | None = None
    progress: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, state: dict) -> "RunResult":
        return cls(
            plan=SearchPlan.from_dict(state["plan"]),
            leads=[Lead.from_dict(d) for d in state.get("leads", [])],
            drafts=[EmailDraft.from_dict(d) for d in state.get("drafts", [])],
            run_dir=Path(state["run_dir"]) if state.get("run_dir") else None,
            progress=state.get("progress", []),
            errors=state.get("errors", []),
        )


class LeadPipeline:
    """Runs the graph end to end without sending (sending needs the interactive CLI or a checkpointer)."""

    def __init__(self, settings: Settings, zoominfo_client, llm: LLMClient | None = None, mock: bool = False):
        self.deps = LeadGenDeps.build(settings, zoominfo_client, llm=llm, mock=mock)
        self.graph = build_graph()

    def run(self, query: str, **options) -> RunResult:
        options["send"] = False
        state = self.graph.invoke(initial_state(query, **options), context=self.deps)
        return RunResult.from_state(state)

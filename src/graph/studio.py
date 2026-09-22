"""Entry point for LangGraph Studio / `langgraph dev`.

The server supplies its own checkpointer, and nodes fall back to LeadGenDeps.from_env()
(live ZoomInfo; missing credentials fail rather than silently using sample data).
Input example: {"query": "companies that need AI training", "options": {"limit": 5}}
"""
from src.graph.builder import build_graph

graph = build_graph()

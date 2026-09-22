"""Loads the company knowledge base (profile + programme catalog)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class KnowledgeBase:
    profile: str
    categories: dict

    def category(self, key: str) -> dict:
        return self.categories.get(key, {})

    def category_keys(self) -> list[str]:
        return list(self.categories.keys())

    def catalog_summary(self) -> str:
        lines = []
        for key, cat in self.categories.items():
            names = "; ".join(p["name"] for p in cat["programmes"])
            lines.append(f"- {key} ({cat['label']}): {names}")
        return "\n".join(lines)


@lru_cache(maxsize=4)
def load_knowledge(knowledge_dir: Path) -> KnowledgeBase:
    profile = (knowledge_dir / "company_profile.md").read_text(encoding="utf-8")
    catalog = json.loads((knowledge_dir / "programmes.json").read_text(encoding="utf-8"))
    return KnowledgeBase(profile=profile, categories=catalog["categories"])

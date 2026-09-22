"""Writes each run to outputs/<timestamp>_<slug>/ : leads.csv, leads.json, emails/*.txt, emails.csv"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.models import EmailDraft, Lead, SearchPlan


def slugify(text: str, max_len: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len] or "run"


def make_run_dir(output_dir: Path, query: str) -> Path:
    from uuid import uuid4
    run_dir = output_dir / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{slugify(query)}_{uuid4().hex[:6]}"
    (run_dir / "emails").mkdir(parents=True, exist_ok=True)
    return run_dir


def export_leads(run_dir: Path, leads: list[Lead], plan: SearchPlan) -> Path:
    csv_path = run_dir / "leads.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "tier", "score", "company", "website", "top_topic", "signal_score",
                    "audience_strength", "signal_date", "all_topics", "contact_name", "contact_title",
                    "contact_email", "contact_phone", "score_reasons"])
        for i, lead in enumerate(leads, 1):
            top, c = lead.top_signal, lead.primary_contact
            w.writerow([
                i, lead.tier, lead.score, lead.company_name, lead.website,
                top.topic if top else "", top.signal_score if top else "",
                top.audience_strength if top else "", top.signal_date if top else "",
                "; ".join(s.topic for s in lead.signals),
                c.full_name if c else "", c.job_title if c else "",
                c.email if c else "", c.phone if c else "",
                " | ".join(lead.score_reasons),
            ])

    (run_dir / "leads.json").write_text(
        json.dumps({"plan": asdict(plan), "leads": [asdict(l) for l in leads]}, indent=2), encoding="utf-8"
    )
    return csv_path


def export_emails(run_dir: Path, drafts: list[EmailDraft]) -> Path:
    csv_path = run_dir / "emails.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "to_name", "to_email", "programme", "subject", "body"])
        for d in drafts:
            w.writerow([d.company_name, d.to_name, d.to_email, d.programme, d.subject, d.body])
            txt = run_dir / "emails" / f"{slugify(d.company_name)}.txt"
            txt.write_text(f"To: {d.to_name} <{d.to_email}>\nSubject: {d.subject}\n\n{d.body}\n", encoding="utf-8")
    return csv_path

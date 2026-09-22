"""WTD Lead Generation CLI

Examples
  python main.py "companies that need AI training"
  python main.py "hot leads for cyber security training in the UK" --limit 20
  python main.py "businesses needing data skills" --mock          # no API keys needed
  python main.py --topics ai                                       # browse ZoomInfo intent topics
  python main.py --lookup countries                                # browse any lookup list
  python main.py "AI training" --send                              # use the signed-in web workspace to send
  python main.py --draw-graph                                      # writes outputs/lead_graph.mmd
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid

from config.settings import settings


def build_client(mock: bool):
    if mock:
        from src.zoominfo.mock_client import MockZoomInfoClient
        return MockZoomInfoClient(settings)
    if not settings.has_zoominfo:
        sys.exit("ZoomInfo credentials missing. Fill in .env, or run with --mock to try it out.")
    from src.zoominfo.client import ZoomInfoClient
    return ZoomInfoClient(settings)


def print_results(result) -> None:
    p = result.plan
    print(f"\nSearch: {p.raw_query}")
    print(f"Category: {p.category_label} | Country: {p.country or 'any'} | Min signal: {p.min_signal_score}")
    print(f"Intent topics: {', '.join(p.intent_topics)}\n")

    if not result.leads:
        print("No leads found. Try a broader query, a lower signal score, or different topics (--topics).")
        return

    print(f"{'#':<3}{'Tier':<6}{'Score':<7}{'Company':<38}{'Top topic':<26}{'Contact'}")
    print("-" * 110)
    for i, lead in enumerate(result.leads, 1):
        top, c = lead.top_signal, lead.primary_contact
        contact = f"{c.full_name}, {c.job_title}" if c else "-"
        print(f"{i:<3}{lead.tier:<6}{lead.score:<7}{lead.company_name[:36]:<38}"
              f"{(top.topic if top else '-')[:24]:<26}{contact[:40]}")

    if result.drafts:
        d = result.drafts[0]
        print(f"\nSample email ({d.company_name})\nTo: {d.to_email}\nSubject: {d.subject}\n\n{d.body}\n")

    if result.errors:
        print("Warnings:\n  " + "\n  ".join(result.errors))
    print(f"Saved to: {result.run_dir}")
    print(f"  leads.csv, leads.json, emails.csv, emails/ ({len(result.drafts)} drafts)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Find companies that need training and draft outreach emails.")
    ap.add_argument("query", nargs="?", help='e.g. "companies that need AI training"')
    ap.add_argument("--limit", type=int, default=10, help="max leads to return (default 10)")
    ap.add_argument("--country", help="override country filter (use --lookup countries for valid values)")
    ap.add_argument("--min-tier", choices=["Cool", "Warm", "Hot"], default="Cool")
    ap.add_argument("--contacts-per-lead", type=int, default=1, help="contacts to enrich per company (credits)")
    ap.add_argument("--no-buyers", action="store_true", help="only use ZoomInfo's recommended contacts")
    ap.add_argument("--no-enrich", action="store_true", help="skip contact enrichment (saves credits, no emails)")
    ap.add_argument("--no-emails", action="store_true", help="find leads only")
    ap.add_argument("--send", action="store_true", help="legacy flag; sending now requires a Microsoft mailbox connection in the web app")
    ap.add_argument("--mock", action="store_true", help="use fictional sample data, no ZoomInfo needed")
    ap.add_argument("--topics", metavar="KEYWORD", nargs="?", const="", help="list ZoomInfo intent topics")
    ap.add_argument("--lookup", metavar="FIELD", help="list values for a lookup field, e.g. countries")
    ap.add_argument("--draw-graph", action="store_true", help="save the flow diagram as Mermaid")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.send:
        ap.error("Email sending requires a Microsoft mailbox connection and draft approval in the web workspace.")

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.draw_graph:
        from src.graph.builder import build_graph
        out = settings.output_dir / "lead_graph.mmd"
        out.write_text(build_graph().get_graph().draw_mermaid())
        print(f"Graph diagram saved to {out} (paste into https://mermaid.live)")
        return

    client = build_client(args.mock)

    if args.topics is not None:
        kw = args.topics.lower()
        topics = [t for t in client.intent_topics() if kw in t.lower()]
        print("\n".join(sorted(topics)) or "No topics matched.")
        return
    if args.lookup:
        for row in client.lookup(args.lookup):
            print(f"{row.get('id')}\t{row.get('attributes', {}).get('name', '')}")
        return
    if not args.query:
        ap.print_help()
        return

    run_graph(args, client)


def run_graph(args, client) -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from src.graph.builder import build_graph, initial_state
    from src.graph.state import LeadGenDeps
    from src.pipeline import RunResult

    graph = build_graph(checkpointer=InMemorySaver())
    deps = LeadGenDeps.build(settings, client, mock=args.mock)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    payload = initial_state(
        args.query,
        limit=args.limit,
        country=args.country,
        find_buyers=not args.no_buyers,
        enrich=not args.no_enrich,
        write_emails=not args.no_emails,
        send=args.send,
        min_tier=args.min_tier,
        contacts_per_lead=args.contacts_per_lead,
    )

    printed_results = False
    while True:
        interrupted = None
        for chunk in graph.stream(payload, config, context=deps, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    interrupted = update[0].value
                    continue
                for line in (update or {}).get("progress", []):
                    print(f"  > {line}")

        state = graph.get_state(config).values
        if not printed_results:
            print_results(RunResult.from_state(state))
            printed_results = True

        if not interrupted:
            break

        payload = Command(resume=ask_for_approval(interrupted))


def ask_for_approval(request: dict) -> dict:
    print(f"\n{request['message']} (full text in {request['run_dir']}/emails)")
    for i, d in enumerate(request["drafts"], 1):
        print(f"  {i}. {d['to']:<40} {d['subject']}")
    answer = input("Send all? [y]es / [n]o / numbers to skip (e.g. 2,3): ").strip().lower()
    if answer in ("y", "yes"):
        return {"approved": True}
    if answer and all(p.strip().isdigit() for p in answer.split(",")):
        skip = {int(p) for p in answer.split(",")}
        exclude = [d["to"] for i, d in enumerate(request["drafts"], 1) if i in skip]
        return {"approved": True, "exclude": exclude}
    return {"approved": False}

if __name__ == "__main__":
    main()

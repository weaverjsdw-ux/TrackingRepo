from __future__ import annotations
import argparse, sys
from datetime import date
from .collect import run_outlook_scan
from .scan import build_threads
from .briefing import render_briefing

def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only JS follow-ups briefing")
    ap.add_argument("--allow", default="",
                    help="semicolon-separated lead SMTP addresses or domains")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--ps", default="scripts/outlook_scan.ps1")
    args = ap.parse_args(argv)
    allow = [a for a in args.allow.split(";") if a]
    records, replies = run_outlook_scan(allow, days=args.days, ps_path=args.ps)
    threads = build_threads(records, replies)
    print(render_briefing(threads, date.today()))
    return 0

if __name__ == "__main__":
    sys.exit(main())

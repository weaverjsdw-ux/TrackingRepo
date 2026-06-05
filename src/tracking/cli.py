"""Command-line runner that ties the pipeline together.

    python -m tracking.cli authorize   # one-time Gmail OAuth consent -> token.json
    python -m tracking.cli pull         # pull labeled emails -> stage -> process -> preview
    python -m tracking.cli write [--commit]   # write processed sends to the Sheet

`authorize` must be run once interactively (it opens a browser). After that the
stored token refreshes silently, so `pull`/`write` run unattended.

Config is read from environment variables (optionally a local .env, see
.env.example). Nothing here is imported by the library modules; it is the thin
edge that wires Gmail intake -> Phase 1 pipeline -> Phase 3 sheet write.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, # comments."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _gmail_source():
    from .gmail_source import GmailSource
    return GmailSource(
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "secrets/client_secret.json"),
        token_path=os.environ.get("GOOGLE_TOKEN_PATH", "secrets/token.json"),
    )


def cmd_authorize(_args) -> int:
    """Trigger the one-time OAuth consent and persist the token."""
    src = _gmail_source()
    src._svc()  # builds the service, runs the local-server consent flow if needed
    print(f"Authorized. Token stored at {os.environ.get('GOOGLE_TOKEN_PATH', 'secrets/token.json')}.")
    return 0


def cmd_pull(args) -> int:
    from . import intake, overview, pipeline
    from .sheet import build_sheet_plan
    from .identify import FileType

    label = os.environ.get("GMAIL_LABEL", "tracking-reports")
    drop = os.environ.get("DROP_ROOT", "./drop")
    staged = intake.pull_and_stage(_gmail_source(), label, drop)

    if not staged:
        print("No new labeled messages to process.")
        return 0

    for s in staged:
        print(f"\n=== {s.folder_name} ===")
        for line in s.log:
            print(f"  {line}")
        if s.pending_reason:
            print(f"  -> PENDING: {s.pending_reason}")
            continue
        print(f"  metrics: {s.result.metrics}")
        # Re-read from the final (moved) folder so PDF/file paths are valid.
        res = pipeline.process_folder(s.drop_folder, s.identity)
        pdf = next((p.source for p in res.planned if p.type is FileType.OVERVIEW_PDF), None)
        if pdf is not None:
            try:
                plan = build_sheet_plan(res, overview.parse_summary(pdf))
                print(f"  sheet values (preview): {plan.values}")
                for w in plan.warnings:
                    print(f"  WARN: {w}")
                for f in plan.flags:
                    print(f"  FLAG: {f}")
            except Exception as exc:  # noqa: BLE001
                print(f"  SHEET PLAN ERROR (would block write): {exc}")
    return 0


def cmd_write(args) -> int:
    from . import filing, overview, pipeline
    from .identify import FileType
    from .sheet import build_sheet_plan, write_send
    from .sheets_writer import GoogleSheetsWriter

    drop = Path(os.environ.get("DROP_ROOT", "./drop"))
    processed = drop / "processed"
    if not processed.is_dir():
        print(f"No processed sends at {processed}.")
        return 0
    # Where the renamed report folders are filed (next to the others). Default:
    # the parent of this project folder (e.g. ...\TRACKINGREPORTS).
    reports_dir = Path(os.environ.get("REPORTS_DIR", str(Path.cwd().parent)))
    writer = GoogleSheetsWriter(
        spreadsheet_id=os.environ.get("SHEET_ID"),
        tab=os.environ.get("SHEET_TAB", "Sheet1"),
        service_account=os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", "secrets/service-account.json"),
    )
    rc = 0
    for folder in sorted(p for p in processed.iterdir() if p.is_dir()):
        result = pipeline.process_folder(folder)
        pdf = next((p.source for p in result.planned if p.type is FileType.OVERVIEW_PDF), None)
        if pdf is None:
            print(f"{folder.name}: no overview PDF; skipping (cannot cross-check).")
            rc = 1
            continue
        summary = overview.parse_summary(pdf)
        plan = build_sheet_plan(result, summary)
        if not args.commit:
            print(f"{folder.name}: DRY-RUN sheet values: {plan.values}")
            continue
        written = write_send(writer, result.identity, plan,
                             fill_blanks_only=not getattr(args, "force", False))
        # Create the renamed report folder (idempotent: skip if already filed).
        out = reports_dir / result.identity.folder_name
        if out.exists():
            filed = "report folder exists"
        else:
            names = filing.write_renamed(result, summary, out)
            filed = f"filed {len(names)} renamed files -> {out}"
        print(f"{folder.name}: wrote {len(written)} cells; {filed}")
    return rc


def cmd_run(args) -> int:
    """One scheduled cycle: pull new sends, then write them to the Sheet."""
    from argparse import Namespace
    rc_pull = cmd_pull(args)
    rc_write = cmd_write(Namespace(commit=True))
    return rc_pull or rc_write


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="tracking.cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("authorize", help="one-time Gmail OAuth consent").set_defaults(func=cmd_authorize)
    sub.add_parser("pull", help="pull labeled emails, stage, process, preview").set_defaults(func=cmd_pull)
    w = sub.add_parser("write", help="write processed sends to the Sheet (dry-run unless --commit)")
    w.add_argument("--commit", action="store_true", help="actually write (default is dry-run)")
    w.add_argument("--force", action="store_true",
                   help="overwrite existing cells (reconcile), not just blanks")
    w.set_defaults(func=cmd_write)
    sub.add_parser("run", help="one cycle for scheduling: pull + write to the Sheet").set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

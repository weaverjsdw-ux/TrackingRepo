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


def _draft_writer():
    return _gmail_source()


def _sfmc_client():
    from . import sfmc
    return sfmc.RealSfmcClient.from_env()


def _drop_root() -> Path:
    return Path(os.environ.get("DROP_ROOT", "./drop"))


def _state_path() -> Path:
    from . import run_state
    return run_state.default_state_path(_drop_root())


def cmd_authorize(_args) -> int:
    """Trigger the one-time OAuth consent and persist the token."""
    src = _gmail_source()
    src._svc()  # builds the service, runs the local-server consent flow if needed
    print(f"Authorized. Token stored at {os.environ.get('GOOGLE_TOKEN_PATH', 'secrets/token.json')}.")
    return 0


def cmd_pull(args) -> int:
    from . import intake, overview, pipeline, run_state
    from .sheet import build_sheet_plan
    from .identify import FileType

    label = os.environ.get("GMAIL_LABEL", "tracking-reports")
    drop = _drop_root()
    staged = intake.pull_and_stage(_gmail_source(), label, drop)
    state_update = run_state.record_staged(_state_path(), staged)
    changed_pending = {s.job_id for s in state_update.changed_pending}

    if not staged:
        print("No new labeled messages to process.")
        return 0

    for s in staged:
        if s.pending_reason and s.job_id not in changed_pending:
            continue
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
    if state_update.unchanged_pending_count:
        label = "send" if state_update.unchanged_pending_count == 1 else "sends"
        print(
            f"{state_update.unchanged_pending_count} unchanged pending {label} "
            "suppressed; run `python -m tracking.cli status` for details."
        )
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
        try:
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
        except Exception as exc:  # noqa: BLE001 - batch command reports per-folder blockers
            print(f"{folder.name}: WRITE ERROR: {exc}")
            rc = 1
    return rc


def cmd_draft_reports(args) -> int:
    from . import contacts, drafts, filing, naming, overview, pipeline
    from .identify import FileType

    processed = _drop_root() / "processed"
    if not processed.is_dir():
        print(f"No processed sends at {processed}.")
        return 0

    contacts_path = Path(os.environ.get("CONTACTS_CSV", "contacts.csv"))
    try:
        contact_rows = contacts.load_contacts(contacts_path)
    except Exception as exc:  # noqa: BLE001 - operator-facing batch command
        print(f"DRAFT ERROR: {exc}")
        return 1
    reports_dir = Path(os.environ.get("REPORTS_DIR", str(Path.cwd().parent)))
    dry_run = getattr(args, "dry_run", False)
    writer = None if dry_run else _draft_writer()

    rc = 0
    for folder in sorted(p for p in processed.iterdir() if p.is_dir()):
        try:
            result = pipeline.process_folder(folder)
            pdf = next((p.source for p in result.planned if p.type is FileType.OVERVIEW_PDF), None)
            if pdf is None:
                raise drafts.DraftError("no overview PDF; cannot draft official report package")
            summary = overview.parse_summary(pdf)
            out = reports_dir / result.identity.folder_name
            contact = contacts.report_contact_for(contact_rows, result.identity)
            if dry_run:
                attachment_names = [
                    name for _source, name in filing.planned_renamed(result, summary)
                ]
                official_pdf = next(
                    (name for name in attachment_names if name.endswith(".pdf")),
                    None,
                )
                if official_pdf is None:
                    raise drafts.DraftError("official overview PDF is not in the report package")
                print(
                    f"{result.identity.folder_name}: DRY-RUN draft to "
                    f"{contact.pc_email} subject "
                    f"{naming.email_subject(result.identity)!r} with "
                    f"{len(attachment_names)} attachments: {', '.join(attachment_names)}"
                )
                continue
            expected_names = [
                name for _source, name in filing.planned_renamed(result, summary)
            ]
            if not out.exists() or any(not (out / name).is_file() for name in expected_names):
                filing.write_renamed(result, summary, out)
            assert writer is not None
            outcome = drafts.create_engagement_draft(
                writer, _state_path(), result.identity, out, contact
            )
            if outcome.created:
                print(f"{result.identity.folder_name}: created draft {outcome.draft_id}")
            else:
                print(f"{result.identity.folder_name}: already drafted {outcome.draft_id}")
        except Exception as exc:  # noqa: BLE001 - operator-facing batch command
            print(f"{folder.name}: DRAFT ERROR: {exc}")
            rc = 1
    return rc


def cmd_run(args) -> int:
    """One scheduled cycle: pull new sends, then write them to the Sheet."""
    from argparse import Namespace
    rc_pull = cmd_pull(args)
    if rc_pull:
        print("Skipping write and draft creation because pull did not complete cleanly.")
        return rc_pull
    rc_write = cmd_write(Namespace(commit=True))
    rc_draft = 0
    if getattr(args, "drafts", False):
        if rc_pull or rc_write:
            print("Skipping draft creation because pull/write did not complete cleanly.")
        else:
            rc_draft = cmd_draft_reports(args)
    return rc_pull or rc_write or rc_draft


def _format_draft_readiness(processed_root: Path, contacts_path: Path) -> str:
    from . import contacts, pipeline
    from .identify import FileType

    if not processed_root.is_dir():
        return "Draft readiness: no processed sends"
    folders = sorted(p for p in processed_root.iterdir() if p.is_dir())
    if not folders:
        return "Draft readiness: no processed sends"

    try:
        contact_rows = contacts.load_contacts(contacts_path)
    except Exception as exc:  # noqa: BLE001 - status should report blockers, not fail
        return f"Draft readiness: blocked ({exc})"

    ready = 0
    blockers: list[str] = []
    for folder in folders:
        try:
            result = pipeline.process_folder(folder)
            pdf = next((p.source for p in result.planned if p.type is FileType.OVERVIEW_PDF), None)
            if pdf is None:
                raise RuntimeError("no overview PDF; cannot draft official report package")
            contacts.report_contact_for(contact_rows, result.identity)
            ready += 1
        except Exception as exc:  # noqa: BLE001 - operator-facing status detail
            blockers.append(f"  {folder.name}: {exc}")

    if blockers:
        return "Draft readiness: blocked\n" + "\n".join(blockers)
    label = "send" if ready == 1 else "sends"
    return f"Draft readiness: ready ({ready} processed {label} have enabled contacts)"


def cmd_status(_args) -> int:
    from . import run_state
    processed_root = _drop_root() / "processed"
    contacts_path = Path(os.environ.get("CONTACTS_CSV", "contacts.csv"))
    print(run_state.format_status(_state_path(), processed_root=processed_root))
    print(_format_draft_readiness(processed_root, contacts_path))
    return 0


def cmd_sfmc_probe(args) -> int:
    from . import sfmc

    result = sfmc.probe_capabilities(_sfmc_client(), args.send_id)
    print(sfmc.format_probe_result(result))
    return 0 if result.ok else 1


def cmd_sfmc_stage(args) -> int:
    from . import naming, sfmc

    identity = naming.SendIdentity(
        client=args.client,
        season=args.season,
        year=args.year,
        type=args.type,
    )
    folder = sfmc.stage_send(_drop_root(), identity, _sfmc_client(), args.send_id)
    count = len([p for p in folder.iterdir() if p.is_file()])
    label = "artifact" if count == 1 else "artifacts"
    print(f"{identity.folder_name}: staged {count} SFMC {label} -> {folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tracking.cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("authorize", help="one-time Gmail OAuth consent").set_defaults(func=cmd_authorize)
    sub.add_parser("pull", help="pull labeled emails, stage, process, preview").set_defaults(func=cmd_pull)
    w = sub.add_parser("write", help="write processed sends to the Sheet (dry-run unless --commit)")
    w.add_argument("--commit", action="store_true", help="actually write (default is dry-run)")
    w.add_argument("--force", action="store_true",
                   help="overwrite existing cells (reconcile), not just blanks")
    w.set_defaults(func=cmd_write)
    d = sub.add_parser("draft-reports", help="create Gmail drafts for processed engagement reports")
    d.add_argument("--dry-run", action="store_true",
                   help="validate recipients and attachments without creating Gmail drafts")
    d.set_defaults(func=cmd_draft_reports)
    r = sub.add_parser("run", help="one cycle for scheduling: pull + write to the Sheet")
    r.add_argument("--drafts", action="store_true",
                   help="also create Gmail drafts after write-back succeeds")
    r.set_defaults(func=cmd_run)
    sub.add_parser("status", help="show last run, pending sends, processed sends, and drafts").set_defaults(func=cmd_status)
    sf = sub.add_parser("sfmc-probe", help="probe SFMC API capabilities for one send")
    sf.add_argument("--send-id", required=True, help="SFMC/ExactTarget send or job identifier to probe")
    sf.set_defaults(func=cmd_sfmc_probe)
    ss = sub.add_parser(
        "sfmc-stage",
        help="fetch SFMC API artifacts into the canonical processed folder",
    )
    ss.add_argument("--send-id", required=True, help="SFMC/ExactTarget send or job identifier to fetch")
    ss.add_argument("--client", required=True, help="client name")
    ss.add_argument("--season", required=True, help="send season")
    ss.add_argument("--year", required=True, help="send year")
    ss.add_argument("--type", required=True, help="send type, for example eNL or ePC")
    ss.set_defaults(func=cmd_sfmc_stage)

    args = parser.parse_args(argv)
    _load_dotenv()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

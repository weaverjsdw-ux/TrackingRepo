"""CLI wiring smoke tests (no Gmail/Sheets creds, no network).

The credential-dependent commands (authorize/pull, and write --commit) need live
creds and are exercised manually; here we cover argument wiring, the .env loader,
and the early-return write path that touches no Google API."""

import json

import pytest
import shutil

from tracking import cli
from tracking import intake, run_state
from tracking import naming
from tracking import sheets_writer
from tracking.intake import StagedSend


_SHEET_HEADERS = [
    "", "Client", "Type", "Issue #",
    "# Total sent", "# Delivered", "# Unique clicks",
    "Booklet landing page unique clicks", "# Total opens", "# Unique opens",
    "Unique\nopen rate %", "Unique click-through %", "Subject line",
    "Actual ... send date",
]
_INTAKE_JOB_ID = "555111"
_INTAKE_EXPORTS = {
    "export_1001.csv": "Send",
    "export_1002.csv": "Open",
    "export_1003.csv": "click",
    "export_1004.csv": "click",
    "export_1005.csv": "bounce",
    "export_1006.csv": "unsub",
}
_INTAKE_PDF = "Job_770001_Overview_20260901.pdf"


class FakeSheetsWriter:
    def __init__(self, *args, **kwargs):
        row = [""] * len(_SHEET_HEADERS)
        row[1] = "Northshore College"
        row[2] = "eNL"
        row[_SHEET_HEADERS.index("Actual ... send date")] = "10/2/2026"
        self.grid = [
            [""] * len(_SHEET_HEADERS),
            [""] * len(_SHEET_HEADERS),
            _SHEET_HEADERS[:],
            row,
        ]

    def get_values(self):
        return [row[:] for row in self.grid]

    def update_cell(self, row, col, value):
        while len(self.grid[row]) <= col:
            self.grid[row].append("")
        self.grid[row][col] = str(value)


class FakeEmailAndDraftSource:
    def __init__(self, messages):
        self._messages = list(messages)
        self.marked: list[str] = []
        self.created_drafts = []

    def fetch_labeled(self, label):
        return [m for m in self._messages if m.id not in self.marked]

    def mark_processed(self, message_id):
        self.marked.append(message_id)

    def create_draft(self, draft):
        self.created_drafts.append(draft)
        return f"draft-{len(self.created_drafts)}"


def _attachment(folder, name):
    return intake.Attachment(name, (folder / name).read_bytes())


def _export_message(folder, message_id, filename, export_type):
    body = (
        f"Your file export is complete. Exported File: {filename} "
        f"Exported Type: {export_type} Exported for - JobID: {_INTAKE_JOB_ID} "
        "A file has been attached."
    )
    return intake.EmailMessage(
        message_id,
        "The export you requested is complete.",
        (_attachment(folder, filename),),
        body,
    )


def _overview_message(folder):
    return intake.EmailMessage(
        "overview",
        "Northshore College Fall 2026 eNL - Engagement Tracking Report",
        (_attachment(folder, _INTAKE_PDF),),
        f"The PDF file for job {_INTAKE_JOB_ID} is attached.",
    )


def _tracking_messages(folder):
    messages = [
        _export_message(folder, f"m{i}", filename, export_type)
        for i, (filename, export_type) in enumerate(_INTAKE_EXPORTS.items())
    ]
    messages.append(_overview_message(folder))
    return messages


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nGMAIL_LABEL=my-label\nDROP_ROOT=./drop\n", encoding="utf-8")
    monkeypatch.delenv("GMAIL_LABEL", raising=False)
    cli._load_dotenv(str(env))
    import os
    assert os.environ["GMAIL_LABEL"] == "my-label"


def test_requires_subcommand_does_not_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GMAIL_LABEL=live-label\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main([])
    import os
    assert "GMAIL_LABEL" not in os.environ


def test_write_with_no_processed_sends_is_noop(tmp_path, monkeypatch, capsys):
    # No processed/ dir -> returns 0 without ever touching the Google API.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path))
    rc = cli.main(["write"])
    assert rc == 0
    assert "No processed sends" in capsys.readouterr().out


def test_write_continues_after_one_processed_folder_errors(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed"
    (processed / "A Bad Send").mkdir(parents=True)
    shutil.copytree(synthetic_send, processed / synthetic_send.name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))

    rc = cli.main(["write"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "A Bad Send: WRITE ERROR:" in out
    assert f"{synthetic_send.name}: DRY-RUN sheet values" in out


def test_write_commit_repairs_existing_report_folder_missing_csvs(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    identity = naming.parse_send_identity(synthetic_send.name)
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    reports_dir = tmp_path / "reports"
    existing = reports_dir / synthetic_send.name
    existing.mkdir(parents=True)
    raw_pdf = next(synthetic_send.glob("*.pdf"))
    shutil.copy2(raw_pdf, existing / naming.finished_pdf_name(identity))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(reports_dir))
    monkeypatch.setattr(sheets_writer, "GoogleSheetsWriter", FakeSheetsWriter)

    rc = cli.main(["write", "--commit"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "repaired report folder" in out
    assert naming.finished_pdf_name(identity) in [path.name for path in existing.iterdir()]
    assert any(path.name.endswith(".csv") for path in existing.iterdir())


def test_status_prints_automation_state(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path))
    run_state.record_staged(
        tmp_path / run_state.STATE_FILE,
        [StagedSend("555111", tmp_path / "inbox" / "job_555111", ["m1"],
                    pending_reason="awaiting overview-PDF email (identity) for this JobID")],
        now="2026-06-11T13:00:00",
    )

    rc = cli.main(["status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Last run: 2026-06-11T13:00:00" in out
    assert "Pending sends: 1" in out
    assert "job 555111" in out


def test_status_reports_missing_contacts_as_draft_blocker(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(tmp_path / "missing-contacts.csv"))

    rc = cli.main(["status"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Draft readiness: blocked" in out
    assert "Contact file not found" in out


def test_status_blocks_draft_readiness_when_overview_pdf_missing(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    for pdf in processed.glob("*.pdf"):
        pdf.unlink()
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Draft readiness: blocked" in out
    assert "no overview PDF" in out


def test_status_blocks_draft_readiness_when_report_package_cannot_be_named(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    bounce = processed / "export_1005.csv"
    lines = bounce.read_text(encoding="utf-8").splitlines()
    bounce.write_text("\n".join([*lines, lines[-1]]) + "\n", encoding="utf-8")
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Draft readiness: blocked" in out
    assert "Cannot name it" in out


def test_status_reports_singular_draft_ready_grammar(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Draft readiness: ready (1 processed send has enabled contact)" in out


def test_status_json_reports_machine_readable_draft_blockers(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,,no\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["status", "--json"])

    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["processed_count"] == 1
    assert data["drafted_count"] == 0
    assert data["pending_count"] == 0
    assert data["draft_readiness"]["state"] == "blocked"
    assert data["draft_readiness"]["blockers"] == [{
        "send": synthetic_send.name,
        "reason": "Report delivery is disabled for client 'Northshore College'.",
    }]


def test_contacts_init_writes_starter_from_processed_sends(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["contacts-init"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote 1 starter contact row" in out
    assert contacts.read_text(encoding="utf-8").splitlines() == [
        "client,pc_email,report_delivery_enabled",
        "Northshore College,,no",
    ]


def test_contacts_init_refuses_to_overwrite_existing_contacts(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    existing = (
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n"
    )
    contacts.write_text(existing, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["contacts-init"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "CONTACTS INIT ERROR" in out
    assert contacts.read_text(encoding="utf-8") == existing


def test_contacts_init_add_missing_preserves_existing_reviewed_rows(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed"
    shutil.copytree(synthetic_send, processed / synthetic_send.name)
    (processed / "Zenith College - Fall 2026 eNL").mkdir(parents=True)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["contacts-init", "--add-missing"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Added 1 missing starter contact row" in out
    assert contacts.read_text(encoding="utf-8").splitlines() == [
        "client,pc_email,report_delivery_enabled",
        "Northshore College,pc@example.com,yes",
        "Zenith College,,no",
    ]


def test_contacts_init_add_missing_leaves_complete_contacts_unchanged(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    existing = (
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n"
    )
    contacts.write_text(existing, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["contacts-init", "--add-missing"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "No missing contact rows" in out
    assert contacts.read_text(encoding="utf-8") == existing


def test_contacts_init_add_missing_handles_contacts_file_without_final_newline(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed"
    shutil.copytree(synthetic_send, processed / synthetic_send.name)
    (processed / "Zenith College - Fall 2026 eNL").mkdir(parents=True)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))

    rc = cli.main(["contacts-init", "--add-missing"])

    assert rc == 0
    assert "Added 1 missing starter contact row" in capsys.readouterr().out
    assert contacts.read_text(encoding="utf-8").splitlines() == [
        "client,pc_email,report_delivery_enabled",
        "Northshore College,pc@example.com,yes",
        "Zenith College,,no",
    ]


def test_pull_suppresses_unchanged_pending_details(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path))
    pending = StagedSend(
        "555111",
        tmp_path / "inbox" / "job_555111",
        ["m1"],
        pending_reason="awaiting overview-PDF email (identity) for this JobID",
    )
    monkeypatch.setattr(cli, "_gmail_source", lambda: object())
    monkeypatch.setattr(intake, "pull_and_stage", lambda source, label, drop: [pending])

    first_rc = cli.main(["pull"])
    first_out = capsys.readouterr().out
    second_rc = cli.main(["pull"])
    second_out = capsys.readouterr().out

    assert first_rc == 0 and second_rc == 0
    assert "awaiting overview-PDF" in first_out
    assert "awaiting overview-PDF" not in second_out
    assert "1 unchanged pending send suppressed" in second_out


def test_draft_reports_creates_engagement_draft_once(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    class FakeDraftWriter:
        def __init__(self):
            self.created = []

        def create_draft(self, draft):
            self.created.append(draft)
            return f"draft-{len(self.created)}"

    writer = FakeDraftWriter()
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_draft_writer", lambda: writer)

    first_rc = cli.main(["draft-reports"])
    first_out = capsys.readouterr().out
    second_rc = cli.main(["draft-reports"])
    second_out = capsys.readouterr().out

    assert first_rc == 0 and second_rc == 0
    assert "created draft draft-1" in first_out
    assert "already drafted draft-1" in second_out
    assert len(writer.created) == 1
    assert writer.created[0].to == ["pc@example.com"]
    assert any(p.name.endswith("Engagement Tracking Report.pdf")
               for p in writer.created[0].attachments)


def test_draft_reports_repairs_existing_report_folder_missing_pdf(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    class FakeDraftWriter:
        def __init__(self):
            self.created = []

        def create_draft(self, draft):
            self.created.append(draft)
            return "draft-1"

    writer = FakeDraftWriter()
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    reports_dir = tmp_path / "reports"
    existing = reports_dir / synthetic_send.name
    existing.mkdir(parents=True)
    (existing / "leftover.csv").write_text("partial", encoding="utf-8")
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(reports_dir))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_draft_writer", lambda: writer)

    rc = cli.main(["draft-reports"])

    assert rc == 0
    assert "created draft draft-1" in capsys.readouterr().out
    assert any(p.name.endswith("Engagement Tracking Report.pdf")
               for p in writer.created[0].attachments)


def test_draft_reports_repairs_existing_report_folder_missing_csvs(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    class FakeDraftWriter:
        def __init__(self):
            self.created = []

        def create_draft(self, draft):
            self.created.append(draft)
            return "draft-1"

    writer = FakeDraftWriter()
    identity = naming.parse_send_identity(synthetic_send.name)
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    reports_dir = tmp_path / "reports"
    existing = reports_dir / synthetic_send.name
    existing.mkdir(parents=True)
    raw_pdf = next(synthetic_send.glob("*.pdf"))
    shutil.copy2(raw_pdf, existing / naming.finished_pdf_name(identity))
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(reports_dir))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_draft_writer", lambda: writer)

    rc = cli.main(["draft-reports"])

    attachment_names = [p.name for p in writer.created[0].attachments]
    assert rc == 0
    assert "created draft draft-1" in capsys.readouterr().out
    assert naming.finished_pdf_name(identity) in attachment_names
    assert any(name.endswith(".csv") for name in attachment_names)


def test_draft_reports_dry_run_plans_without_gmail(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(reports_dir))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_draft_writer", lambda: pytest.fail("Gmail should not be touched"))

    rc = cli.main(["draft-reports", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN draft to pc@example.com" in out
    assert "Engagement Tracking — Northshore College Fall 2026 eNL" in out
    assert "Engagement Tracking Report.pdf" in out
    assert not reports_dir.exists()


def test_draft_reports_dry_run_prepare_files_writes_report_folder_without_gmail(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(reports_dir))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_draft_writer", lambda: pytest.fail("Gmail should not be touched"))

    rc = cli.main(["draft-reports", "--dry-run", "--prepare-files"])

    out = capsys.readouterr().out
    report_folder = reports_dir / synthetic_send.name
    assert rc == 0
    assert "DRY-RUN prepared" in out
    assert report_folder.is_dir()
    assert (report_folder / "Northshore College Fall 2026 eNL - Engagement Tracking Report.pdf").is_file()
    assert any(path.name.endswith(".csv") for path in report_folder.iterdir())


def test_draft_reports_prepare_files_requires_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setattr(cli, "_draft_writer", lambda: pytest.fail("Gmail should not be touched"))

    rc = cli.main(["draft-reports", "--prepare-files"])

    assert rc == 1
    assert "--prepare-files requires --dry-run" in capsys.readouterr().out


def test_draft_reports_missing_contact_file_blocks_cleanly(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    processed = tmp_path / "drop" / "processed" / synthetic_send.name
    shutil.copytree(synthetic_send, processed)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("CONTACTS_CSV", str(tmp_path / "missing-contacts.csv"))

    rc = cli.main(["draft-reports"])

    assert rc == 1
    assert "DRAFT ERROR: Contact file not found" in capsys.readouterr().out


def test_run_drafts_skips_drafts_when_write_fails(monkeypatch):
    calls = []

    def fake_pull(args):
        calls.append("pull")
        return 0

    def fake_write(args):
        calls.append("write")
        return 1

    def fake_draft(args):
        calls.append("draft")
        return 0

    monkeypatch.setattr(cli, "cmd_pull", fake_pull)
    monkeypatch.setattr(cli, "cmd_write", fake_write)
    monkeypatch.setattr(cli, "cmd_draft_reports", fake_draft)

    rc = cli.main(["run", "--drafts"])

    assert rc == 1
    assert calls == ["pull", "write"]


def test_run_skips_write_and_drafts_when_pull_fails(monkeypatch, capsys):
    calls = []

    def fake_pull(args):
        calls.append("pull")
        return 1

    def fake_write(args):
        calls.append("write")
        return 0

    def fake_draft(args):
        calls.append("draft")
        return 0

    monkeypatch.setattr(cli, "cmd_pull", fake_pull)
    monkeypatch.setattr(cli, "cmd_write", fake_write)
    monkeypatch.setattr(cli, "cmd_draft_reports", fake_draft)

    rc = cli.main(["run", "--drafts"])

    assert rc == 1
    assert calls == ["pull"]
    assert "Skipping write and draft creation" in capsys.readouterr().out


def test_run_drafts_processes_one_send_end_to_end_with_fakes(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    source = FakeEmailAndDraftSource(_tracking_messages(synthetic_send))
    contacts = tmp_path / "contacts.csv"
    contacts.write_text(
        "client,pc_email,report_delivery_enabled\n"
        "Northshore College,pc@example.com,yes\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("CONTACTS_CSV", str(contacts))
    monkeypatch.setattr(cli, "_gmail_source", lambda: source)
    monkeypatch.setattr(cli, "_draft_writer", lambda: source)
    monkeypatch.setattr(sheets_writer, "GoogleSheetsWriter", FakeSheetsWriter)

    first_rc = cli.main(["run", "--drafts"])
    first_out = capsys.readouterr().out
    second_rc = cli.main(["run", "--drafts"])
    second_out = capsys.readouterr().out

    report_folder = tmp_path / "reports" / synthetic_send.name
    state = run_state.load_state(tmp_path / "drop" / run_state.STATE_FILE)
    assert first_rc == 0 and second_rc == 0
    assert "created draft draft-1" in first_out
    assert "No new labeled messages to process." in second_out
    assert len(source.created_drafts) == 1
    assert len(source.marked) == len(_INTAKE_EXPORTS) + 1
    assert (tmp_path / "drop" / "processed" / synthetic_send.name).is_dir()
    assert (report_folder / "Northshore College Fall 2026 eNL - Engagement Tracking Report.pdf").is_file()
    assert any(path.name.endswith(".csv") for path in report_folder.iterdir())
    assert state["drafts"][synthetic_send.name]["draft_id"] == "draft-1"


def test_sfmc_probe_prints_fake_probe_result(monkeypatch, capsys):
    class FakeSfmcClient:
        def authenticate(self):
            return True

        def find_send(self, send_id):
            return True

        def tracking_count(self, send_id, metric):
            return 1

        def overview_pdf_available(self, send_id):
            return True

    monkeypatch.setattr(cli, "_sfmc_client", lambda: FakeSfmcClient(), raising=False)

    rc = cli.main(["sfmc-probe", "--send-id", "12345"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "SFMC probe for send 12345: OK" in out
    assert "overview PDF: available" in out


def test_sfmc_stage_uses_fake_source_adapter(tmp_path, monkeypatch, capsys, synthetic_send):
    class FakeSfmcClient:
        def authenticate(self):
            return True

        def find_send(self, send_id):
            return True

        def tracking_count(self, send_id, metric):
            return 1

        def overview_pdf_available(self, send_id):
            return True

        def fetch_artifacts(self, send_id):
            assert send_id == "12345"
            from tracking import sfmc
            return [
                sfmc.SfmcArtifact(path.name, path.read_bytes())
                for path in sorted(synthetic_send.iterdir())
                if path.is_file()
            ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setattr(cli, "_sfmc_client", lambda: FakeSfmcClient(), raising=False)

    rc = cli.main([
        "sfmc-stage",
        "--send-id", "12345",
        "--client", "Northshore College",
        "--season", "Fall",
        "--year", "2026",
        "--type", "eNL",
    ])

    assert rc == 0
    folder = tmp_path / "drop" / "processed" / "Northshore College - Fall 2026 eNL"
    assert (folder / "export_1001.csv").read_bytes() == (
        synthetic_send / "export_1001.csv"
    ).read_bytes()
    assert "staged 8 SFMC artifacts" in capsys.readouterr().out


def test_sfmc_stage_blocks_when_probe_requires_ui_fallback(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    class FakeSfmcClient:
        def __init__(self):
            self.fetched = False

        def authenticate(self):
            return True

        def find_send(self, send_id):
            return True

        def tracking_count(self, send_id, metric):
            return 1

        def overview_pdf_available(self, send_id):
            return False

        def fetch_artifacts(self, send_id):
            self.fetched = True
            from tracking import sfmc
            return [
                sfmc.SfmcArtifact(path.name, path.read_bytes())
                for path in sorted(synthetic_send.iterdir())
                if path.is_file()
            ]

    client = FakeSfmcClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setattr(cli, "_sfmc_client", lambda: client, raising=False)

    rc = cli.main([
        "sfmc-stage",
        "--send-id", "12345",
        "--client", "Northshore College",
        "--season", "Fall",
        "--year", "2026",
        "--type", "eNL",
    ])

    out = capsys.readouterr().out
    assert rc == 1
    assert client.fetched is False
    assert "SFMC probe for send 12345: BLOCKED" in out
    assert "official overview PDF unavailable" in out
    assert not (tmp_path / "drop" / "processed" / "Northshore College - Fall 2026 eNL").exists()


def test_sfmc_stage_reports_existing_processed_folder_without_force(
    tmp_path, monkeypatch, capsys, synthetic_send
):
    class FakeSfmcClient:
        def authenticate(self):
            return True

        def find_send(self, send_id):
            return True

        def tracking_count(self, send_id, metric):
            return 1

        def overview_pdf_available(self, send_id):
            return True

        def fetch_artifacts(self, send_id):
            from tracking import sfmc
            return [
                sfmc.SfmcArtifact(path.name, path.read_bytes())
                for path in sorted(synthetic_send.iterdir())
                if path.is_file()
            ]

    existing = tmp_path / "drop" / "processed" / "Northshore College - Fall 2026 eNL"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DROP_ROOT", str(tmp_path / "drop"))
    monkeypatch.setattr(cli, "_sfmc_client", lambda: FakeSfmcClient(), raising=False)

    rc = cli.main([
        "sfmc-stage",
        "--send-id", "12345",
        "--client", "Northshore College",
        "--season", "Fall",
        "--year", "2026",
        "--type", "eNL",
    ])

    out = capsys.readouterr().out
    assert rc == 1
    assert "SFMC STAGE ERROR" in out
    assert "already exists" in out
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"

"""CLI wiring smoke tests (no Gmail/Sheets creds, no network).

The credential-dependent commands (authorize/pull, and write --commit) need live
creds and are exercised manually; here we cover argument wiring, the .env loader,
and the early-return write path that touches no Google API."""

import pytest
import shutil

from tracking import cli
from tracking import intake, run_state
from tracking.intake import StagedSend


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
    assert "Engagement Tracking Report.pdf" in out
    assert not reports_dir.exists()


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

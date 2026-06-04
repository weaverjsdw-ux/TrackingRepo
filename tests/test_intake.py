"""Phase 2 intake tests against a fake EmailSource (no Gmail creds, no network).

Fakes mirror the real shapes: ExactTarget "Email Export" notifications (one
attachment each, JobID + Exported Type in the body) and the operator's overview-
PDF email (identity in the subject, 'for job <n>' in the body). Covers JobID
grouping, identity-from-overview, dedup, idempotency, and the await-overview /
incomplete pending paths (BRIEF §1, §3)."""

from pathlib import Path

from tracking import intake
from tracking.intake import Attachment, EmailMessage

JOB = "555111"
PREFIX = "Northshore College Fall 2026 eNL"      # overview-subject prefix form
FOLDER = "Northshore College - Fall 2026 eNL"    # resulting drop/processed folder

# synthetic file -> ExactTarget "Exported Type"
EXPORTS = {
    "export_1001.csv": "Send",
    "export_1002.csv": "Open",
    "export_1003.csv": "click",   # master Unique Clicks (many links)
    "export_1004.csv": "click",   # Request Your (one link) — same type, told apart by content
    "export_1005.csv": "bounce",
    "export_1006.csv": "unsub",
}
PDF = "Job_770001_Overview_20260901.pdf"


def _att(folder: Path, name: str) -> Attachment:
    return Attachment(name, (folder / name).read_bytes())


def _export_msg(folder: Path, mid: str, fname: str, etype: str, job=JOB) -> EmailMessage:
    body = (f"Your file export is complete. Exported File: {fname} "
            f"Exported Type: {etype} Exported for - JobID: {job} A file has been attached.")
    return EmailMessage(mid, "The export you requested is complete.", (_att(folder, fname),), body)


def _overview_msg(folder: Path, mid="ov", job=JOB) -> EmailMessage:
    return EmailMessage(mid, f"{PREFIX} - Engagement Tracking Report",
                        (_att(folder, PDF),), f"The PDF file for job {job} is attached.")


def _all_msgs(folder: Path) -> list[EmailMessage]:
    msgs = [_export_msg(folder, f"m{i}", f, t) for i, (f, t) in enumerate(EXPORTS.items())]
    msgs.append(_overview_msg(folder))
    return msgs


class FakeGmail:
    def __init__(self, messages):
        self._messages = list(messages)
        self.marked: list[str] = []

    def fetch_labeled(self, label):
        return [m for m in self._messages if m.id not in self.marked]

    def mark_processed(self, message_id):
        self.marked.append(message_id)


# --- classification ---

def test_parse_export_and_overview_emails(synthetic_send):
    ex = intake.parse_export_email(_export_msg(synthetic_send, "m", "export_1003.csv", "click"))
    assert ex == (JOB, "click")
    job, ident = intake.parse_overview_email(_overview_msg(synthetic_send))
    assert job == JOB
    assert ident.folder_name == FOLDER


def test_non_report_email_is_ignored(synthetic_send):
    junk = EmailMessage("j1", "Lunch?", (), "no job here")
    assert intake.parse_export_email(junk) is None
    assert intake.parse_overview_email(junk) is None


# --- orchestration ---

def test_happy_path_groups_by_jobid_and_moves(synthetic_send, tmp_path):
    src = FakeGmail(_all_msgs(synthetic_send))
    staged = intake.pull_and_stage(src, "tracking-reports", tmp_path)

    assert len(staged) == 1
    s = staged[0]
    assert s.job_id == JOB
    assert s.pending_reason is None
    assert s.identity.folder_name == FOLDER
    assert s.result.metrics["BH"] == 3                      # request file present -> primary
    assert s.result.bh.method == "request-file"
    assert s.drop_folder == tmp_path / "processed" / FOLDER
    assert not (tmp_path / "inbox" / f"job_{JOB}").exists()
    assert len(src.marked) == len(EXPORTS) + 1              # all emails marked


def test_idempotent_rerun(synthetic_send, tmp_path):
    src = FakeGmail(_all_msgs(synthetic_send))
    intake.pull_and_stage(src, "tracking-reports", tmp_path)
    marked_after_first = list(src.marked)
    assert intake.pull_and_stage(src, "tracking-reports", tmp_path) == []
    assert src.marked == marked_after_first                 # nothing double-marked


def test_duplicate_export_email_staged_once(synthetic_send, tmp_path):
    dup = _export_msg(synthetic_send, "dup", "export_1001.csv", "Send")
    same = _export_msg(synthetic_send, "dup2", "export_1001.csv", "Send")
    src = FakeGmail(_all_msgs(synthetic_send) + [same])  # export_1001 delivered twice
    staged = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    files = [p for p in staged[0].drop_folder.glob("*") if p.is_file()]
    assert len(files) == len(EXPORTS) + 1                   # csvs + pdf, no dupes


def test_pending_until_overview_arrives(synthetic_send, tmp_path):
    # Exports arrive first, no overview -> no identity -> pending; not marked.
    exports_only = [_export_msg(synthetic_send, f"m{i}", f, t)
                    for i, (f, t) in enumerate(EXPORTS.items())]
    src = FakeGmail(exports_only)
    first = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    assert first[0].pending_reason and "overview" in first[0].pending_reason
    assert src.marked == []
    assert (tmp_path / "inbox" / f"job_{JOB}").exists()

    # Overview (identity) arrives -> completes.
    src._messages.append(_overview_msg(synthetic_send))
    second = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    assert second[0].pending_reason is None
    assert second[0].result.metrics["BH"] == 3
    assert len(src.marked) == len(EXPORTS) + 1

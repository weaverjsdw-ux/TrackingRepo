"""Phase 2 intake orchestration tests, against a fake EmailSource (no Gmail
creds, no network). Exercises staging, dedup, idempotency, and the delayed-
arrival 'pending' path (BRIEF §1, §3)."""

from pathlib import Path

import pytest

from tracking import intake
from tracking.intake import Attachment, EmailMessage

FOLDER = "Northshore College - Fall 2026 eNL"
SUBJECT = FOLDER  # default subject_parser assumes the folder-name form


class FakeGmail:
    """In-memory EmailSource backed by EmailMessage objects."""

    def __init__(self, messages: list[EmailMessage]):
        self._messages = messages
        self.marked: list[str] = []

    def fetch_labeled(self, label: str) -> list[EmailMessage]:
        # Only return not-yet-marked messages (mirrors a label being removed).
        return [m for m in self._messages if m.id not in self.marked]

    def mark_processed(self, message_id: str) -> None:
        self.marked.append(message_id)


def _attachments_from(folder: Path, names: list[str]) -> tuple[Attachment, ...]:
    return tuple(Attachment(n, (folder / n).read_bytes()) for n in names)


# The non-lead-scoring synthetic files (lead scoring is out of the pipeline).
# Core = Sent, Opens, Clicks, overview PDF (the completeness gate); the rest
# (booklet, bounce, unsub) are optional.
CORE_FILES = [
    "export_1001.csv", "export_1002.csv", "export_1003.csv",
    "Job_770001_Overview_20260901.pdf",
]
OPTIONAL_FILES = ["export_1004.csv", "export_1005.csv", "export_1006.csv"]
ALL_FILES = CORE_FILES + OPTIONAL_FILES


def test_happy_path_stages_processes_and_moves(synthetic_send, tmp_path):
    msg = EmailMessage("m1", SUBJECT, _attachments_from(synthetic_send, ALL_FILES))
    src = FakeGmail([msg])

    staged = intake.pull_and_stage(src, "tracking-reports", tmp_path)

    assert len(staged) == 1
    s = staged[0]
    assert s.pending_reason is None
    assert s.result is not None and s.result.metrics["BH"] == 3
    # Completed send moved to processed/, removed from inbox.
    assert s.drop_folder == tmp_path / "processed" / FOLDER
    assert s.drop_folder.is_dir()
    assert not (tmp_path / "inbox" / FOLDER).exists()
    assert src.marked == ["m1"]


def test_idempotent_rerun_does_nothing(synthetic_send, tmp_path):
    msg = EmailMessage("m1", SUBJECT, _attachments_from(synthetic_send, ALL_FILES))
    src = FakeGmail([msg])
    intake.pull_and_stage(src, "tracking-reports", tmp_path)

    # Second pull: message already marked/processed -> nothing new staged.
    again = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    assert again == []
    assert src.marked == ["m1"]  # not double-marked


def test_duplicate_attachment_saved_once(synthetic_send, tmp_path):
    # Same file delivered twice (two emails) -> staged once (content-hash dedup).
    atts = _attachments_from(synthetic_send, ALL_FILES)
    src = FakeGmail([
        EmailMessage("m1", SUBJECT, atts),
        EmailMessage("m2", SUBJECT, atts),  # exact duplicates
    ])
    staged = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    files = [p for p in staged[0].drop_folder.glob("*") if p.is_file()]
    assert len(files) == len(ALL_FILES)  # no duplicates written (csvs + pdf)


def test_delayed_arrival_pends_then_completes(synthetic_send, tmp_path):
    """A send split across two emails: the first arrival lacks core files
    (Clicks + overview PDF), so the completeness gate keeps it pending; the
    second arrival completes it. Models out-of-order/delayed delivery (BRIEF §1).
    Uses the real default completeness gate (no injected stub)."""
    first_batch = ["export_1001.csv", "export_1002.csv"]            # Sent + Opens only
    rest = [f for f in ALL_FILES if f not in first_batch]           # incl. Clicks + PDF

    src = FakeGmail([EmailMessage("m1", SUBJECT, _attachments_from(synthetic_send, first_batch))])
    first = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    assert first[0].pending_reason is not None
    assert "Unique Clicks" in first[0].pending_reason and "Overview PDF" in first[0].pending_reason
    assert src.marked == []  # not marked while pending
    assert (tmp_path / "inbox" / FOLDER).exists()  # stays in inbox

    # Rest of the send arrives.
    src._messages.append(EmailMessage("m2", SUBJECT, _attachments_from(synthetic_send, rest)))
    second = intake.pull_and_stage(src, "tracking-reports", tmp_path)
    done = second[0]
    assert done.pending_reason is None
    assert done.result.metrics["BH"] == 3
    # Both contributing messages now marked (m1 was pending, m2 completed it).
    assert set(src.marked) == {"m1", "m2"}

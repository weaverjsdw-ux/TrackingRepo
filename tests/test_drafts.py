"""Gmail draft planning for engagement report delivery."""

import pytest

from tracking.contacts import Contact
from tracking.drafts import DraftError, build_engagement_draft, create_engagement_draft
from tracking.naming import SendIdentity


IDENTITY = SendIdentity("Northshore College", "Fall", "2026", "eNL")
CONTACT = Contact(client="Northshore College", pc_email="pc@example.com",
                  report_delivery_enabled=True)


class FakeDraftWriter:
    def __init__(self):
        self.created = []

    def create_draft(self, draft):
        self.created.append(draft)
        return f"draft-{len(self.created)}"


def _report_dir(tmp_path):
    folder = tmp_path / IDENTITY.folder_name
    folder.mkdir()
    for suffix in [
        "Engagement Tracking Report.pdf",
        "Total Sent.csv",
        "Unique Opens.csv",
        "Unique Clicks.csv",
        "Request Your.csv",
    ]:
        (folder / f"{IDENTITY.prefix} - {suffix}").write_text("x", encoding="utf-8")
    return folder


def test_build_engagement_draft_includes_reports_and_official_pdf(tmp_path):
    draft = build_engagement_draft(IDENTITY, _report_dir(tmp_path), CONTACT)

    assert draft.to == ["pc@example.com"]
    assert draft.subject == "Engagement Tracking — Northshore College Fall 2026 eNL"
    names = [p.name for p in draft.attachments]
    assert f"{IDENTITY.prefix} - Engagement Tracking Report.pdf" in names
    assert f"{IDENTITY.prefix} - Total Sent.csv" in names


def test_build_engagement_draft_requires_official_pdf(tmp_path):
    folder = _report_dir(tmp_path)
    (folder / f"{IDENTITY.prefix} - Engagement Tracking Report.pdf").unlink()

    with pytest.raises(DraftError, match="overview PDF"):
        build_engagement_draft(IDENTITY, folder, CONTACT)


def test_build_engagement_draft_ignores_lead_score_files(tmp_path):
    folder = _report_dir(tmp_path)
    (folder / "sd_Northshore College - Lead Scoring20260901.csv").write_text("score", encoding="utf-8")

    draft = build_engagement_draft(IDENTITY, folder, CONTACT)

    assert not any("Lead Scoring" in p.name for p in draft.attachments)


def test_build_engagement_draft_ignores_unrelated_report_folder_files(tmp_path):
    folder = _report_dir(tmp_path)
    (folder / "leftover.csv").write_text("partial", encoding="utf-8")
    (folder / "old overview.pdf").write_text("old", encoding="utf-8")

    draft = build_engagement_draft(IDENTITY, folder, CONTACT)

    names = [p.name for p in draft.attachments]
    assert "leftover.csv" not in names
    assert "old overview.pdf" not in names


def test_create_engagement_draft_is_idempotent(tmp_path):
    state_file = tmp_path / "state.json"
    writer = FakeDraftWriter()
    folder = _report_dir(tmp_path)

    first = create_engagement_draft(writer, state_file, IDENTITY, folder, CONTACT)
    second = create_engagement_draft(writer, state_file, IDENTITY, folder, CONTACT)

    assert first.created is True
    assert first.draft_id == "draft-1"
    assert second.created is False
    assert second.draft_id == "draft-1"
    assert len(writer.created) == 1

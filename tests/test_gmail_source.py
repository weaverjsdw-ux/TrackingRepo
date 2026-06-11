"""Structural tests for the real Gmail adapter (no network, no google libs).

Live behavior is exercised against a real inbox; here we only assert the adapter
loads without the optional google dependencies and fails with a helpful message
if they (and creds) are absent."""

import pytest

from tracking.drafts import DraftEmail, DraftWriter
from tracking import intake
from tracking.gmail_source import GmailSource, _build_draft_message


def test_adapter_satisfies_email_source_shape():
    src = GmailSource(client_secret=None, token_path="nonexistent/token.json")
    assert hasattr(src, "fetch_labeled") and hasattr(src, "mark_processed")
    assert isinstance(src, intake.EmailSource)  # runtime Protocol check
    assert hasattr(src, "create_draft")
    assert isinstance(src, DraftWriter)


def test_build_service_without_libs_or_creds_fails_loud(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_TOKEN_PATH", raising=False)
    src = GmailSource(client_secret=None, token_path="nonexistent/token.json")
    # Either google libs are absent (extra not installed) or creds are missing;
    # both must raise a clear RuntimeError rather than silently no-op.
    with pytest.raises(RuntimeError):
        src._build_service()


def test_build_draft_message_contains_recipient_subject_and_attachment(tmp_path):
    attachment = tmp_path / "Northshore College Fall 2026 eNL - Total Sent.csv"
    attachment.write_text("Email Address\nperson@example.com\n", encoding="utf-8")
    draft = DraftEmail(
        to=["pc@example.com"],
        subject="Engagement Tracking — Northshore College Fall 2026 eNL",
        body="Attached are the reports.",
        attachments=[attachment],
    )

    raw = _build_draft_message(draft)

    import base64
    decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8", "replace")
    assert "To: pc@example.com" in decoded
    assert "Subject:" in decoded and "Northshore College Fall 2026 eNL" in decoded
    assert "Northshore College Fall 2026 eNL - Total Sent.csv" in decoded

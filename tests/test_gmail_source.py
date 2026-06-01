"""Structural tests for the real Gmail adapter (no network, no google libs).

Live behavior is exercised against a real inbox; here we only assert the adapter
loads without the optional google dependencies and fails with a helpful message
if they (and creds) are absent."""

import pytest

from tracking import intake
from tracking.gmail_source import GmailSource


def test_adapter_satisfies_email_source_shape():
    src = GmailSource(client_secret=None, token_path="nonexistent/token.json")
    assert hasattr(src, "fetch_labeled") and hasattr(src, "mark_processed")
    assert isinstance(src, intake.EmailSource)  # runtime Protocol check


def test_build_service_without_libs_or_creds_fails_loud():
    src = GmailSource(client_secret=None, token_path="nonexistent/token.json")
    # Either google libs are absent (extra not installed) or creds are missing;
    # both must raise a clear RuntimeError rather than silently no-op.
    with pytest.raises(RuntimeError):
        src._build_service()

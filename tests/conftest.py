"""Shared test fixtures + the synthetic/real split.

Synthetic fixtures (committed, no PII) always run. The real Bradley example
folder lives OUTSIDE the repo and is git-ignored; tests marked `realdata` run
against it when present and skip otherwise (e.g. in CI) -- so no PII ever
touches the repo or CI (BRIEF §0).
"""

from __future__ import annotations

import sys
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SYNTHETIC_SEND = REPO_ROOT / "tests" / "fixtures" / "synthetic" / "Northshore College - Fall 2026 eNL"

# Real example folder: ../Bradley University - Spring 2026 eNL (outside the repo).
REAL_SEND = REPO_ROOT.parent / "Bradley University - Spring 2026 eNL"

LIVE_ENV_KEYS = (
    "GMAIL_LABEL",
    "DROP_ROOT",
    "REPORTS_DIR",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_TOKEN_PATH",
    "GOOGLE_SHEETS_SERVICE_ACCOUNT",
    "SHEET_ID",
    "SHEET_TAB",
    "CONTACTS_CSV",
    "SFMC_AUTH_BASE_URL",
    "SFMC_CLIENT_ID",
    "SFMC_CLIENT_SECRET",
    "SFMC_ACCOUNT_ID",
    "SFMC_REST_BASE_URL",
    "SFMC_SEND_LOOKUP_URL",
    "SFMC_OVERVIEW_PDF_URL",
    "SFMC_TRACKING_SENT_URL",
    "SFMC_TRACKING_OPEN_URL",
    "SFMC_TRACKING_CLICK_URL",
    "SFMC_TRACKING_BOUNCE_URL",
    "SFMC_TRACKING_UNSUB_URL",
    "SFMC_ARTIFACT_SENT_URL",
    "SFMC_ARTIFACT_OPEN_URL",
    "SFMC_ARTIFACT_CLICK_URL",
    "SFMC_ARTIFACT_BOUNCE_URL",
    "SFMC_ARTIFACT_UNSUB_URL",
    "SFMC_ARTIFACT_OVERVIEW_PDF_URL",
    "SFMC_ARTIFACT_BOOKLET_URL",
)


@pytest.fixture(autouse=True)
def isolate_live_env(monkeypatch) -> None:
    """Keep tests from inheriting live Gmail/Sheets/drop-folder config."""
    for key in LIVE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Repo-local temp dirs avoid cross-chat/global AppData temp collisions."""
    root = REPO_ROOT / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def synthetic_send() -> Path:
    return SYNTHETIC_SEND


@pytest.fixture
def real_send() -> Path:
    if not REAL_SEND.is_dir():
        pytest.skip(f"real example folder not present (expected at {REAL_SEND})")
    return REAL_SEND

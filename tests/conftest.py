"""Shared test fixtures + the synthetic/real split.

Synthetic fixtures (committed, no PII) always run. The real Bradley example
folder lives OUTSIDE the repo and is git-ignored; tests marked `realdata` run
against it when present and skip otherwise (e.g. in CI) -- so no PII ever
touches the repo or CI (BRIEF §0).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SYNTHETIC_SEND = REPO_ROOT / "tests" / "fixtures" / "synthetic" / "Northshore College - Fall 2026 eNL"

# Real example folder: ../Bradley University - Spring 2026 eNL (outside the repo).
REAL_SEND = REPO_ROOT.parent / "Bradley University - Spring 2026 eNL"


@pytest.fixture
def synthetic_send() -> Path:
    return SYNTHETIC_SEND


@pytest.fixture
def real_send() -> Path:
    if not REAL_SEND.is_dir():
        pytest.skip(f"real example folder not present (expected at {REAL_SEND})")
    return REAL_SEND

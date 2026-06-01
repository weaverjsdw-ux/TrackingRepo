"""Tolerant CSV reading + the counting primitives.

The metric exports are subscriber-level row lists, so the metric value is the
*data row count* (BRIEF §1.1, §2.A §6/§8). These exports carry ExactTarget
quirks -- a UTF-8 BOM, quoted fields with embedded commas, embedded newlines,
diacritics -- so all reading goes through one tolerant reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

LINK_CLICKED_COLUMN = "Link Clicked"


def read_csv(path: str | Path) -> pd.DataFrame:
    """Read an export robustly.

    - utf-8-sig transparently strips a BOM if present (and is a no-op if not).
    - dtype=str + keep_default_na=False: never coerce blanks to NaN or numbers
      to floats; a row is a row regardless of its contents.
    - the C parser handles RFC-4180 quoting: embedded commas and newlines inside
      quoted fields do not inflate the row count.
    """
    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        skip_blank_lines=False,
    )


def read_header(path: str | Path) -> list[str]:
    """Return the column names only, with the same BOM tolerance as read_csv."""
    df = pd.read_csv(path, nrows=0, dtype=str, encoding="utf-8-sig")
    return [str(c) for c in df.columns]


def row_count(path: str | Path) -> int:
    """Number of subscriber-level data rows = the metric value."""
    return len(read_csv(path))


def normalize_link(url: str) -> str:
    """Canonicalize a click URL for grouping/classification.

    Drops the query string and fragment (ExactTarget appends per-recipient utm_*
    and sfmc_id params), strips a trailing slash, and lowercases. Grouping and
    article/system matching all use this form; the original URL is kept
    separately for human-facing description derivation.
    """
    s = re.sub(r"[?#].*$", "", str(url).strip())
    s = s.rstrip("/")
    return s.lower()


def link_counts(path: str | Path) -> dict[str, int]:
    """Map normalized Link Clicked -> unique-click row count for a click export.

    Returns counts keyed by the *normalized* link. Use representative_links() to
    recover a display URL per group.
    """
    df = read_csv(path)
    if LINK_CLICKED_COLUMN not in df.columns:
        raise ValueError(
            f"{Path(path).name}: expected a {LINK_CLICKED_COLUMN!r} column "
            f"(not a click export?). Found: {list(df.columns)}"
        )
    counts: dict[str, int] = {}
    for raw in df[LINK_CLICKED_COLUMN]:
        key = normalize_link(raw)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def representative_links(path: str | Path) -> dict[str, str]:
    """Map normalized link -> the first original (un-lowercased, de-paramed) URL
    seen for it, so descriptions keep human-friendly casing."""
    df = read_csv(path)
    reps: dict[str, str] = {}
    for raw in df.get(LINK_CLICKED_COLUMN, []):
        key = normalize_link(raw)
        if not key or key in reps:
            continue
        reps[key] = re.sub(r"[?#].*$", "", str(raw).strip()).rstrip("/")
    return reps

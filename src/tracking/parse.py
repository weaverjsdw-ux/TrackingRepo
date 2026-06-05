"""Tolerant CSV reading + the counting primitives.

The metric exports are subscriber-level row lists, so the metric value is the
*data row count* (BRIEF §1.1, §2.A §6/§8). These exports carry ExactTarget
quirks -- a UTF-8 BOM, quoted fields with embedded commas, embedded newlines,
diacritics, and occasionally a RAGGED row (an unescaped comma giving a row an
extra field). All reading uses Python's csv module, which tolerates ragged rows
(a strict parser errors on the whole file). Counting never drops a row.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

LINK_CLICKED_COLUMN = "Link Clicked"

# ExactTarget exports can have a very long quoted field on a single line.
csv.field_size_limit(10_000_000)


def _open(path: str | Path):
    # utf-8-sig strips a BOM if present; newline="" lets csv handle embedded newlines.
    return open(path, newline="", encoding="utf-8-sig")


def read_csv(path: str | Path) -> pd.DataFrame:
    """Read an export into a DataFrame (field access; tolerant of bad lines).
    Counting uses row_count(), not this."""
    return pd.read_csv(
        path, dtype=str, keep_default_na=False, encoding="utf-8-sig",
        skip_blank_lines=False, engine="python", on_bad_lines="skip",
    )


def read_header(path: str | Path) -> list[str]:
    """Return the column names only (BOM-tolerant, never errors on data rows)."""
    with _open(path) as fh:
        header = next(csv.reader(fh), [])
    return [str(c) for c in header]


def row_count(path: str | Path) -> int:
    """Number of subscriber-level data rows = the metric value. Counts every
    non-blank data row, including ragged ones (never drops a subscriber)."""
    with _open(path) as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        return sum(1 for row in reader if any(field.strip() for field in row))


def normalize_link(url: str) -> str:
    """Canonicalize a click URL for grouping/classification.

    Drops the query string and fragment (ExactTarget appends per-recipient utm_*
    and sfmc_id params), strips a trailing slash, and lowercases."""
    s = re.sub(r"[?#].*$", "", str(url).strip())
    return s.rstrip("/").lower()


def link_counts(path: str | Path) -> dict[str, int]:
    """Map normalized Link Clicked -> unique-click row count for a click export."""
    with _open(path) as fh:
        reader = csv.DictReader(fh)
        if LINK_CLICKED_COLUMN not in (reader.fieldnames or []):
            raise ValueError(
                f"{Path(path).name}: expected a {LINK_CLICKED_COLUMN!r} column "
                f"(not a click export?). Found: {reader.fieldnames}"
            )
        counts: dict[str, int] = {}
        for row in reader:
            key = normalize_link(row.get(LINK_CLICKED_COLUMN) or "")
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def representative_links(path: str | Path) -> dict[str, str]:
    """Map normalized link -> the first original (de-paramed) URL seen for it,
    so descriptions keep human-friendly casing."""
    reps: dict[str, str] = {}
    with _open(path) as fh:
        reader = csv.DictReader(fh)
        if LINK_CLICKED_COLUMN not in (reader.fieldnames or []):
            return reps
        for row in reader:
            raw = row.get(LINK_CLICKED_COLUMN) or ""
            key = normalize_link(raw)
            if key and key not in reps:
                reps[key] = re.sub(r"[?#].*$", "", str(raw).strip()).rstrip("/")
    return reps

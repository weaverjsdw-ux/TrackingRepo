"""Parse the overview PDF's Summary block into expected per-metric totals.

The overview PDF prints authoritative totals (Total Sent, Hard/Soft/Block
Bounce, Unique Opens, Unique Clicks, Unsubscribes). Phase 3 uses these to
cross-check the file set before writing the Sheet, so a forgotten export fails
loud instead of being written as a silent 0 (operator decision).

The PDF reflects whichever snapshot it was generated with, so its counts may
drift by a few rows from the delivered files (e.g. Unique Opens 2,146 in the PDF
vs 2,138 in a slightly-earlier finished file). Callers therefore use these for
PRESENCE checks (loud on missing-but-expected) and DRIFT warnings, not exact
equality.
"""

from __future__ import annotations

import re
from pathlib import Path

# label -> regex capturing its count in the Summary text. Numbers may carry
# thousands separators ("8,272").
_PATTERNS = {
    "Total Sent": r"Total Sent\s*:\s*([\d,]+)",
    "Hard Bounces": r"Hard Bounce\s*:\s*([\d,]+)",
    "Soft Bounces": r"Soft Bounce\s*:\s*([\d,]+)",
    "Block Bounces": r"Block Bounce\s*:\s*([\d,]+)",
    "Unique Opens": r"Unique Opens\s*:\s*([\d,]+)",
    # Inbox Activity table row "Clicks <total> <unique>" -> take the unique col.
    "Unique Clicks": r"\bClicks\s+([\d,]+)\s+([\d,]+)",
    # Inbox Activity table row "Unsubscribes - <unique>".
    "Unsubscribes": r"Unsubscribes\s+-?\s*([\d,]+)",
    # Written straight to the Sheet (no row-count file exists for these):
    "Delivered": r"Delivered\s*:\s*([\d,]+)",
    "Total Opens": r"Total Opens\s*:\s*([\d,]+)",
}


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def parse_summary(pdf_path: str | Path | bytes) -> dict[str, object]:
    """Return {label -> value} for the PDF summary: integer counts (Total Sent,
    Delivered, Total Opens, ...) plus text fields 'Subject' (newsletter subject)
    and 'Name' (the send identity, e.g. 'Bradley University - Spring 2026 eNL').

    Accepts a file path or the PDF bytes (so an email attachment can be parsed
    in memory)."""
    import io

    import pdfplumber

    src = io.BytesIO(pdf_path) if isinstance(pdf_path, (bytes, bytearray)) else pdf_path
    with pdfplumber.open(src) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    out: dict[str, object] = {}
    for label, pattern in _PATTERNS.items():
        m = re.search(pattern, text)
        if not m:
            continue
        # Unique Clicks pattern captures (total, unique); take the last group.
        out[label] = _to_int(m.group(m.lastindex))
    # Text fields. "Name :" is the send identity; "Subject :" is the newsletter subject.
    name = re.search(r"(?m)^\s*Name\s*:\s*(.+?)\s*$", text)
    if name:
        out["Name"] = name.group(1).strip()
    subj = re.search(r"(?m)^\s*Subject\s*:\s*(.+?)\s*$", text)
    if subj:
        out["Subject"] = subj.group(1).strip()
    return out

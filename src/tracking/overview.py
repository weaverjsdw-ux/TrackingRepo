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
}


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def parse_summary(pdf_path: str | Path) -> dict[str, int]:
    """Return {metric label -> count} for every metric found in the PDF summary."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    out: dict[str, int] = {}
    for label, pattern in _PATTERNS.items():
        m = re.search(pattern, text)
        if not m:
            continue
        # Unique Clicks pattern captures (total, unique); take the last group.
        out[label] = _to_int(m.group(m.lastindex))
    return out

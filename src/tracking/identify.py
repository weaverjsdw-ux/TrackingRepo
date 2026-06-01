"""Content-based file identification (BRIEF §1.1: identify by content, NEVER by
filename -- the raw downloads are named export_<digits>.csv / Job_<digits>...).

Each file is classified by its column-header signature, and the two click
exports (which share an identical header) are split by their distinct
Link-Clicked count: many distinct links -> the master Unique Clicks export;
exactly one -> a per-link booklet click file (operator decision this round).

Bounce sub-typing (Hard/Soft/Block share a byte-identical header and differ only
by Bounce Reason/Description content) is intentionally DEFERRED to Phase 2 -- the
Phase 1 trial scope is Opens + Clicks + BH (BRIEF §6). Bounce files are
identified to the BOUNCE family here; sub-typing is a flagged hardening item.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import parse


class FileType(str, Enum):
    TOTAL_SENT = "Total Sent"
    UNIQUE_OPENS = "Unique Opens"
    UNIQUE_CLICKS = "Unique Clicks"  # master: many distinct links
    BOOKLET_CLICKS = "Booklet Clicks"  # per-link file: one distinct link
    BOUNCE = "Bounce"  # family; Hard/Soft/Block sub-typing deferred (Phase 2)
    UNSUBSCRIBES = "Unsubscribes"
    LEAD_SCORING = "Lead Scoring"  # never renamed (BRIEF §2.B)
    OVERVIEW_PDF = "Overview PDF"


@dataclass(frozen=True)
class Identified:
    path: Path
    type: FileType
    distinct_links: int | None = None  # set for click families


def _norm(col: str) -> str:
    return col.strip().lower()


def identify(path: str | Path) -> Identified:
    """Classify one file by content. Raises loudly on an unrecognized file
    (BRIEF §3: never silently mis-handle)."""
    p = Path(path)

    if p.suffix.lower() == ".pdf":
        if _is_overview_pdf(p):
            return Identified(p, FileType.OVERVIEW_PDF)
        raise ValueError(f"Unrecognized PDF (not an overview report): {p.name}")

    if p.suffix.lower() != ".csv":
        raise ValueError(f"Unrecognized file type {p.suffix!r}: {p.name}")

    header = parse.read_header(p)
    cols = {_norm(c) for c in header}

    # Lead scoring has a wholly different header (no Email Address / SF Status).
    if {"score", "requested booklet"} <= cols and "sf status" not in cols:
        return Identified(p, FileType.LEAD_SCORING)

    # Click family: identical header for master vs per-link; split by content.
    if "link clicked" in cols:
        distinct = len(parse.link_counts(p))
        ftype = FileType.UNIQUE_CLICKS if distinct > 1 else FileType.BOOKLET_CLICKS
        return Identified(p, ftype, distinct_links=distinct)

    if "time opened" in cols:
        return Identified(p, FileType.UNIQUE_OPENS)

    if "unsubscribed time" in cols:
        return Identified(p, FileType.UNSUBSCRIBES)

    if {"bounce reason", "bounce description"} & cols:
        return Identified(p, FileType.BOUNCE)

    # Total Sent is the base signature: ends at SF Status, no event columns.
    if "sf status" in cols and not (
        {"time opened", "link clicked", "unsubscribed time", "bounce reason"} & cols
    ):
        return Identified(p, FileType.TOTAL_SENT)

    raise ValueError(
        f"Unrecognized CSV (no known metric signature): {p.name}\n"
        f"  columns: {header}"
    )


def _is_overview_pdf(path: Path) -> bool:
    """Identify the overview PDF by content (its first page mentions the job
    overview), not by its Job_<digits>_Overview name."""
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - dependency check
        # Without pdfplumber we cannot confirm by content; fail loud rather than
        # guess from the filename.
        raise RuntimeError(
            "pdfplumber is required to identify the overview PDF by content."
        )
    with pdfplumber.open(path) as pdf:
        text = (pdf.pages[0].extract_text() or "").lower() if pdf.pages else ""
    return "overview" in text or "sent" in text

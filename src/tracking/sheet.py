"""Phase 3 — Sheet write-back planning + safe matching (BRIEF §2.A §11).

Mapped to the LIVE '2026 Print Status Report' tab (headers confirmed on row
index 2; Client in col B). The tool writes exactly these six value columns:

    "# Total sent"                          <- Total Sent file row count
    "# Delivered"                           <- overview PDF
    "# Unique clicks"                       <- Unique Clicks file row count
    "Booklet landing page unique clicks"    <- BH (request export; operator-authoritative)
    "# Total opens"                         <- overview PDF
    "# Unique opens"                        <- Unique Opens file row count

There are no bounce/unsub columns on this tab, and the "%" rate columns are left
to the sheet (not written).

Safety (operator decisions):
  * a value the overview PDF reports as nonzero but whose source is absent FAILS
    LOUD (never a silent 0); BH==0 fails loud; BH via the clicks-derive fallback
    is FLAGGED.
  * row match = Client + Type; if >1, tiebreak by the AJ send-date month ->
    season; if still ambiguous, FAIL LOUD and list candidates.
  * fill blanks only -- a cell that already holds a different value is SKIPPED
    and flagged, never overwritten.

build_sheet_plan / write_send are credential-free and unit-tested with a
FakeSheet; sheets_writer.GoogleSheetsWriter is the live SheetWriter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .naming import SendIdentity
from .pipeline import SendResult

# Sheet column header -> how its value is sourced.
#   ("file", <metric>)  = result.metrics[<metric>] (a row count)
#   ("pdf",  <metric>)  = pdf_summary[<metric>]
#   ("bh",   None)      = result.metrics["BH"] (special: 0 fails loud, fallback flagged)
COLUMN_SOURCES = {
    "# Total sent": ("file", "Total Sent"),
    "# Delivered": ("pdf", "Delivered"),
    "# Unique clicks": ("file", "Unique Clicks"),
    "Booklet landing page unique clicks": ("bh", None),
    "# Total opens": ("pdf", "Total Opens"),
    "# Unique opens": ("file", "Unique Opens"),
}

# Cross-check: PDF metric -> the file metric that must exist if the PDF says >0.
_FILE_METRIC_PDF_KEY = {"Total Sent": "Total Sent", "Unique Clicks": "Unique Clicks",
                        "Unique Opens": "Unique Opens"}

_SEASON_MONTHS = {
    "winter": {12, 1, 2}, "spring": {3, 4, 5},
    "summer": {6, 7, 8}, "fall": {9, 10, 11}, "autumn": {9, 10, 11},
}


class SheetError(ValueError):
    """Raised on any unsafe-to-write condition (missing expected value, BH zero,
    or a row/column that cannot be matched unambiguously)."""


@runtime_checkable
class SheetWriter(Protocol):
    def get_values(self) -> list[list[str]]: ...
    def update_cell(self, row: int, col: int, value) -> None: ...


@dataclass
class SheetPlan:
    values: dict[str, int] = field(default_factory=dict)  # sheet header -> value
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def build_sheet_plan(result: SendResult, pdf_summary: dict[str, int]) -> SheetPlan:
    plan = SheetPlan()

    # Guard: a metric the PDF reports as present but with no source file -> loud.
    for metric, pdf_key in _FILE_METRIC_PDF_KEY.items():
        expected = pdf_summary.get(pdf_key)
        if metric not in result.metrics and expected and expected > 0:
            raise SheetError(
                f"Overview PDF reports {pdf_key}={expected} but no {metric} file "
                f"was parsed for this send. Refusing to write a silent 0."
            )

    for header, (kind, key) in COLUMN_SOURCES.items():
        if kind == "file":
            value = result.metrics.get(key)
            if value is None:
                raise SheetError(f"No value for {header!r} ({key} file missing).")
            plan.values[header] = value
            expected = pdf_summary.get(key)
            if expected is not None and expected != value:
                plan.warnings.append(
                    f"{header}: file={value} vs overview PDF={expected} "
                    f"(snapshot drift); writing the file count."
                )
        elif kind == "pdf":
            value = pdf_summary.get(key)
            if value is None:
                raise SheetError(
                    f"No value for {header!r}: overview PDF has no {key}. "
                    f"Refusing to write a silent 0."
                )
            plan.values[header] = value
        elif kind == "bh":
            bh_val = result.metrics.get("BH")
            if bh_val is None:
                raise SheetError("No BH was computed for this send; refusing to write.")
            if bh_val == 0:
                raise SheetError(
                    "BH computed as 0; refusing to write a zero booklet value "
                    "without operator confirmation."
                )
            plan.values[header] = bh_val
            if result.bh is not None and result.bh.method.startswith("clicks-derive"):
                plan.flags.append(
                    f"{header}={bh_val} via FALLBACK ({result.bh.method}) — no request "
                    f"export present; verify the booklet link before relying on it."
                )
    return plan


def _norm(s: str) -> str:
    return str(s).strip().lower()


def _month_to_season(month: int) -> str | None:
    for season, months in _SEASON_MONTHS.items():
        if month in months:
            return season
    return None


def _send_date_season(cell: str) -> str | None:
    """Season implied by an AJ send-date cell like '4/13/2026' -> 'spring'."""
    m = re.search(r"\b(\d{1,2})/\d{1,2}/\d{2,4}\b", str(cell))
    return _month_to_season(int(m.group(1))) if m else None


def _find_col(headers: list[str], *, exact: str | None = None, contains: str | None = None) -> int | None:
    for i, h in enumerate(headers):
        hn = _norm(h)
        if exact is not None and hn == _norm(exact):
            return i
        if contains is not None and _norm(contains) in hn:
            return i
    return None


def write_send(
    writer: SheetWriter,
    identity: SendIdentity,
    plan: SheetPlan,
    *,
    header_row: int = 2,
    client_header: str = "Client",
    type_header: str = "Type",
    send_date_contains: str = "send date",
    fill_blanks_only: bool = True,
) -> dict[str, tuple[int, int]]:
    """Match the send's row (Client+Type, season tiebreak) and write its blank
    metric cells. Returns {header -> (row, col)} actually written."""
    grid = writer.get_values()
    if header_row >= len(grid):
        raise SheetError(f"Header row {header_row} out of range ({len(grid)} rows).")
    headers = grid[header_row]

    col_of: dict[str, int] = {}
    for header in plan.values:
        idx = _find_col(headers, exact=header)
        if idx is None:
            raise SheetError(
                f"No column header matching {header!r}. Confirm the live header "
                f"labels (headers seen near col 55+: {headers[55:64]})."
            )
        col_of[header] = idx

    client_col = _find_col(headers, contains=client_header)
    type_col = _find_col(headers, exact=type_header)
    if client_col is None or type_col is None:
        raise SheetError(f"Missing Client/Type column (Client={client_col}, Type={type_col}).")

    target_client, target_type = _norm(identity.client), _norm(identity.type)
    matches = [
        r for r in range(len(grid)) if r != header_row
        and _norm(_cell(grid, r, client_col)) == target_client
        and _norm(_cell(grid, r, type_col)) == target_type
    ]
    if not matches:
        raise SheetError(
            f"No row for client={identity.client!r} type={identity.type!r}. "
            f"Refusing to write to the wrong row."
        )
    if len(matches) > 1:
        matches = _narrow_by_season(grid, matches, identity, headers, send_date_contains)

    row_idx = matches[0]

    written: dict[str, tuple[int, int]] = {}
    for header, value in plan.values.items():
        col = col_of[header]
        current = _cell(grid, row_idx, col)
        if fill_blanks_only and current.strip() and current.strip() != str(value):
            plan.warnings.append(
                f"{header}: cell already holds {current!r} (≠ {value}); left as-is."
            )
            continue
        writer.update_cell(row_idx, col, value)
        written[header] = (row_idx, col)
    return written


def _narrow_by_season(grid, matches, identity, headers, send_date_contains) -> list[int]:
    season = identity.season.lower()
    date_col = _find_col(headers, contains=send_date_contains)
    if date_col is None:
        raise SheetError(
            f"{len(matches)} rows match client={identity.client!r} type={identity.type!r} "
            f"and no send-date column to disambiguate by season. Operator must pick "
            f"(rows {[m + 1 for m in matches]})."
        )
    narrowed = [r for r in matches if _send_date_season(_cell(grid, r, date_col)) == season]
    if len(narrowed) == 1:
        return narrowed
    candidates = [(m + 1, _cell(grid, m, date_col)) for m in matches]
    raise SheetError(
        f"Ambiguous row for client={identity.client!r} type={identity.type!r} "
        f"season={identity.season!r}: {len(narrowed) or len(matches)} candidates "
        f"(row,send-date)={candidates}. Refusing to guess; operator must confirm."
    )


def _cell(grid: list[list[str]], row: int, col: int) -> str:
    r = grid[row]
    return r[col] if col < len(r) else ""

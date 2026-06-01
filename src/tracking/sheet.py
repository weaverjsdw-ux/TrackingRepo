"""Phase 3 — Sheet write-back planning + safe matching (BRIEF §2.A §11).

Two responsibilities, both credential-free and unit-tested with a FakeSheet:

  1. build_sheet_plan(): turn a processed send + the overview-PDF summary into the
     exact metric values to write, with the safety cross-check the operator asked
     for -- a metric the PDF reports as nonzero but whose file is absent FAILS
     LOUD (never a silent 0); the three identical-header bounce files are
     sub-typed by matching their row counts to the PDF's Hard/Soft/Block totals;
     BH derived via the clicks-derive fallback is FLAGGED; BH == 0 fails loud.

  2. write_send(): match the client ROW and each metric COLUMN by header text on
     the sheet (not fixed letters -- BRIEF §2.A §11), then write. Missing
     client row or metric column fails loud. Writing identical values is
     idempotent.

The live Google Sheets adapter (sheets_writer.GoogleSheetsWriter) implements the
same SheetWriter interface; this module never imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .naming import SendIdentity
from .pipeline import SendResult
from .identify import FileType

CORE_METRICS = ("Total Sent", "Unique Opens", "Unique Clicks", "Unsubscribes")
BOUNCE_METRICS = ("Hard Bounces", "Soft Bounces", "Block Bounces")


class SheetError(ValueError):
    """Raised on any unsafe-to-write condition (missing expected metric,
    unmatched bounce file, BH zero, or a row/column that cannot be matched)."""


@runtime_checkable
class SheetWriter(Protocol):
    def get_values(self) -> list[list[str]]: ...
    def update_cell(self, row: int, col: int, value) -> None: ...


@dataclass
class SheetPlan:
    values: dict[str, int] = field(default_factory=dict)  # metric label -> value to write
    warnings: list[str] = field(default_factory=list)     # e.g. snapshot drift
    flags: list[str] = field(default_factory=list)        # e.g. BH via fallback


def build_sheet_plan(result: SendResult, pdf_summary: dict[str, int]) -> SheetPlan:
    plan = SheetPlan()

    _resolve_core(result, pdf_summary, plan)
    _resolve_bounces(result, pdf_summary, plan)
    _resolve_bh(result, plan)
    return plan


def _resolve_core(result: SendResult, pdf: dict[str, int], plan: SheetPlan) -> None:
    for label in CORE_METRICS:
        file_val = result.metrics.get(label)
        expected = pdf.get(label)
        if file_val is None:
            if expected and expected > 0:
                raise SheetError(
                    f"Overview PDF reports {label}={expected} but no {label} file "
                    f"was parsed for this send. Refusing to write a silent 0."
                )
            plan.values[label] = 0
            plan.flags.append(f"{label}: absent and PDF reports 0/none; writing 0.")
            continue
        plan.values[label] = file_val
        if expected is not None and expected != file_val:
            plan.warnings.append(
                f"{label}: file row count={file_val} vs overview PDF={expected} "
                f"(snapshot drift); writing the file count."
            )


def _resolve_bounces(result: SendResult, pdf: dict[str, int], plan: SheetPlan) -> None:
    """Sub-type the identical-header bounce files by matching their row counts to
    the PDF's Hard/Soft/Block totals (operator decision)."""
    remaining = [p.count for p in result.planned if p.type is FileType.BOUNCE]
    for label in BOUNCE_METRICS:
        expected = pdf.get(label, 0)
        if expected and expected > 0:
            if expected in remaining:
                remaining.remove(expected)
                plan.values[label] = expected
            else:
                raise SheetError(
                    f"Overview PDF reports {label}={expected} but no bounce file "
                    f"with {expected} rows was provided (bounce files: "
                    f"{[p.count for p in result.planned if p.type is FileType.BOUNCE]}). "
                    f"Refusing to write a silent 0."
                )
        else:
            plan.values[label] = 0
    if remaining:
        raise SheetError(
            f"Bounce file(s) with row counts {remaining} do not match any "
            f"Hard/Soft/Block total in the overview PDF; operator review needed "
            f"(possible snapshot drift or a mis-identified file)."
        )


def _resolve_bh(result: SendResult, plan: SheetPlan) -> None:
    bh_val = result.metrics.get("BH")
    if bh_val is None:
        raise SheetError("No BH was computed for this send; refusing to write.")
    if bh_val == 0:
        raise SheetError(
            "BH computed as 0; refusing to write a zero booklet value without "
            "operator confirmation (BRIEF: never write a wrong/placeholder number)."
        )
    plan.values["BH"] = bh_val
    if result.bh is not None and result.bh.method.startswith("clicks-derive"):
        plan.flags.append(
            f"BH={bh_val} via FALLBACK ({result.bh.method}) — no request file "
            f"present; verify the booklet link in the per-run log."
        )


def _norm(s: str) -> str:
    return str(s).strip().lower()


def write_send(
    writer: SheetWriter,
    identity: SendIdentity,
    plan: SheetPlan,
    *,
    header_row: int = 0,
    client_header: str = "Client",
    header_overrides: dict[str, str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Match client row × metric-header columns and write the plan's values.

    header_overrides maps a metric label -> the live sheet's actual header text,
    for when the live labels differ from our canonical ones (the coder confirms
    these against the live sheet once granted access -- flagged open item).
    Returns {label -> (row, col)} of the cells written.
    """
    grid = writer.get_values()
    if header_row >= len(grid):
        raise SheetError(f"Header row {header_row} is out of range (sheet has {len(grid)} rows).")
    headers = grid[header_row]
    overrides = header_overrides or {}

    col_of: dict[str, int] = {}
    for label in plan.values:
        target = overrides.get(label, label)
        idx = next((i for i, h in enumerate(headers) if _norm(h) == _norm(target)), None)
        if idx is None:
            raise SheetError(
                f"No column header matching {target!r} for metric {label!r}. "
                f"Confirm the live header labels (headers seen: {headers})."
            )
        col_of[label] = idx

    client_col = next((i for i, h in enumerate(headers) if _norm(client_header) in _norm(h)), None)
    if client_col is None:
        raise SheetError(f"No client column (header containing {client_header!r}) found: {headers}")

    target_client = _norm(identity.client)
    row_idx = next(
        (r for r in range(len(grid)) if r != header_row
         and _norm(grid[r][client_col]) == target_client),
        None,
    )
    if row_idx is None:
        raise SheetError(
            f"Client row for {identity.client!r} not found in client column "
            f"{client_col}. Refusing to write to the wrong row."
        )

    written: dict[str, tuple[int, int]] = {}
    for label, value in plan.values.items():
        writer.update_cell(row_idx, col_of[label], value)
        written[label] = (row_idx, col_of[label])
    return written

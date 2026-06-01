"""Phase 1 orchestration: a folder of raw exports -> a dry-run plan of what would
be renamed/filed and the metric values that would go into the Sheet.

Phase 1 is folder-based and non-destructive by design (BRIEF §6): it produces a
PLAN (raw path -> finished name, metric -> count, BH + chosen link) plus an
auditable per-run log. It does not move files unless apply_plan() is called, so
every run is reversible.

Per-send input set (operator decision): 7 metric files (Total Sent, Unique
Opens, Unique Clicks, Hard/Soft/Block Bounces, Unsubscribes) + the overview PDF
+ the request (booklet) file. Lead scoring is OUT of the pipeline -- it arrives
in its own email and is neither pulled nor renamed here (deferred phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import bh, naming, parse
from .identify import FileType, Identified, identify

# Identified type -> the Sheet/description label, where it is a fixed metric.
_TYPE_TO_DESCRIPTION = {
    FileType.TOTAL_SENT: "Total Sent",
    FileType.UNIQUE_OPENS: "Unique Opens",
    FileType.UNIQUE_CLICKS: "Unique Clicks",
    FileType.UNSUBSCRIBES: "Unsubscribes",
}


@dataclass
class PlannedFile:
    source: Path
    type: FileType
    finished_name: str | None  # None => not renamed/filed (e.g. ignored input)
    count: int | None  # row count where meaningful


@dataclass
class SendResult:
    identity: naming.SendIdentity
    planned: list[PlannedFile] = field(default_factory=list)
    ignored: list[Path] = field(default_factory=list)  # e.g. lead scoring
    metrics: dict[str, int] = field(default_factory=dict)  # description -> count
    bh: bh.BHResult | None = None
    log: list[str] = field(default_factory=list)


def process_folder(folder: str | Path) -> SendResult:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Send folder not found: {folder}")

    identity = naming.parse_send_identity(folder.name)
    result = SendResult(identity=identity)
    result.log.append(f"Send: {identity.prefix}  (folder: {folder.name})")

    master_clicks: Path | None = None
    request_file: Path | None = None

    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        ident = identify(path)

        if ident.type is FileType.LEAD_SCORING:
            result.ignored.append(path)
            result.log.append(f"  {path.name}  ->  [Lead Scoring] IGNORED "
                              f"(delivered separately; out of pipeline scope)")
            continue

        planned = _plan_file(ident, identity)
        result.planned.append(planned)
        if ident.type is FileType.UNIQUE_CLICKS:
            master_clicks = path
        if ident.type is FileType.BOOKLET_CLICKS:
            request_file = path
        if planned.finished_name and (desc := _TYPE_TO_DESCRIPTION.get(ident.type)):
            result.metrics[desc] = planned.count

        result.log.append(
            f"  {path.name}  ->  [{ident.type.value}]  "
            + (f"'{planned.finished_name}'" if planned.finished_name else "(not renamed)")
            + (f"  rows={planned.count}" if planned.count is not None else "")
        )

    _resolve_bh(result, master_clicks, request_file)
    _validate(result)
    return result


def _plan_file(ident: Identified, identity: naming.SendIdentity) -> PlannedFile:
    t = ident.type
    if t is FileType.OVERVIEW_PDF:
        return PlannedFile(ident.path, t, naming.finished_pdf_name(identity), count=None)
    if t is FileType.BOOKLET_CLICKS:
        return PlannedFile(
            ident.path, t,
            naming.finished_csv_name(identity, naming.REQUEST_FILE_DESCRIPTION),
            count=parse.row_count(ident.path),
        )
    if t is FileType.BOUNCE:
        # Sub-typing (Hard/Soft/Block) deferred to Phase 2; count is the row count.
        return PlannedFile(ident.path, t, finished_name=None, count=parse.row_count(ident.path))
    desc = _TYPE_TO_DESCRIPTION.get(t)
    name = naming.finished_csv_name(identity, desc) if desc else None
    return PlannedFile(ident.path, t, name, count=parse.row_count(ident.path))


def _resolve_bh(result: SendResult, master: Path | None, request: Path | None) -> None:
    """BH via the operator's two paths: request-file primary, clicks-derive
    fallback (BRIEF §2.A §11a)."""
    if master is None and request is None:
        return  # no click data at all; _validate will not invent a BH

    request_count = parse.row_count(request) if request is not None else None
    request_link = None
    if request is not None:
        reps = parse.representative_links(request)
        request_link = next(iter(reps.values()), None)
    master_counts = parse.link_counts(master) if master is not None else None

    result.bh = bh.resolve_bh(request_count, request_link, master_counts)
    result.metrics["BH"] = result.bh.bh
    result.log.extend("  " + line for line in result.bh.log_lines)


# Core metrics that must be present for a send to be "complete" enough to
# process/file (operator decision). Bounces/Unsubscribes/request file are
# optional and may be legitimately zero/absent (flagged, not required).
CORE_METRICS = ("Total Sent", "Unique Opens", "Unique Clicks")


def assess_completeness(result: SendResult) -> tuple[bool, list[str]]:
    """Is this send complete enough to process safely (BRIEF §3 validation gate)?

    Requires the core metrics + the overview PDF. Returns (is_complete, missing).
    Phase 2 intake uses this to keep a delayed/partial send pending rather than
    acting on an incomplete set."""
    missing = [m for m in CORE_METRICS if m not in result.metrics]
    if not any(p.type is FileType.OVERVIEW_PDF for p in result.planned):
        missing.append("Overview PDF")
    return (not missing, missing)


def _validate(result: SendResult) -> None:
    """Surface absent/zero metrics explicitly rather than silently (BRIEF §3
    'Validation gates'). Phase 1 only flags; it does not fabricate values."""
    for desc, count in result.metrics.items():
        if count == 0:
            result.log.append(f"  NOTE: metric '{desc}' is zero/absent.")

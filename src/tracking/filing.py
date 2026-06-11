"""Write the renamed deliverable files for a processed send (BRIEF §1.1, §6
"rename + file all reports to convention").

The pipeline computes each file's finished name but is non-destructive; this step
copies each staged export to its finished name in an output folder. Bounce files
(identical headers) are sub-typed Hard/Soft/Block by matching their row counts to
the overview PDF's totals, then named accordingly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import naming
from .identify import FileType
from .pipeline import SendResult

_BOUNCE_LABELS = ("Hard Bounces", "Soft Bounces", "Block Bounces")


def _subtype_bounces(result: SendResult, pdf_summary: dict) -> dict[Path, str]:
    """Map each bounce file's source path -> 'Hard/Soft/Block Bounces' by matching
    its row count to the PDF's labeled bounce totals. Fails loud on a mismatch."""
    pool = [(lbl, pdf_summary[lbl]) for lbl in _BOUNCE_LABELS
            if pdf_summary.get(lbl)]  # only labels the PDF reports > 0
    mapping: dict[Path, str] = {}
    for p in result.planned:
        if p.type is not FileType.BOUNCE:
            continue
        match = next((item for item in pool if item[1] == p.count), None)
        if match is None:
            raise ValueError(
                f"Bounce file {p.source.name} has {p.count} rows, which matches no "
                f"Hard/Soft/Block total in the overview PDF ({pool}). Cannot name it."
            )
        pool.remove(match)
        mapping[p.source] = match[0]
    return mapping


def planned_renamed(result: SendResult, pdf_summary: dict) -> list[tuple[Path, str]]:
    """Return every deliverable as (source path, finished name), without copying."""
    bounce_map = _subtype_bounces(result, pdf_summary)

    planned: list[tuple[Path, str]] = []
    for p in result.planned:
        if p.type is FileType.BOUNCE:
            name = naming.finished_csv_name(result.identity, bounce_map[p.source])
        else:
            name = p.finished_name
        if not name:
            continue
        planned.append((p.source, name))
    return sorted(planned, key=lambda item: item[1])


def write_renamed(result: SendResult, pdf_summary: dict, out_dir: str | Path) -> list[str]:
    """Copy every deliverable to <out_dir>/<finished name>. Returns the names
    written. Files with no finished name (e.g. ignored lead scoring) are skipped."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for source, name in planned_renamed(result, pdf_summary):
        shutil.copy2(source, out_dir / name)
        written.append(name)
    return sorted(written)

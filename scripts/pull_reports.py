"""pull_reports.py — manifest-driven SFMC report pull.

One saved tool that pulls a selected send from Salesforce Marketing Cloud and
produces its full report package (engagement CSVs, styled PDF, Lead Scoring
export, Print Status Report row, calendar mark, Gmail drafts). All per-run
facts live in runs/<run-id>/manifest.json; none are hardcoded.

See docs/superpowers/specs/2026-07-15-sfmc-report-pull-design.md.

Run with the repo venv:  ./.venv/Scripts/python.exe scripts/pull_reports.py <cmd>
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

# Make the installed `tracking` package importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ── booklet selector defaults by send type (a RULE; the value is confirmed
# per send in the manifest). eNL newsletters tag the booklet link v=enlA;
# eQC quarterlies use the /requestguide URL; ePC postcards vary (cID) so no
# default — the operator supplies it.
_BOOKLET_DEFAULTS = {"enl": "v=enlA", "eqc": "/requestguide"}


def default_run_id() -> str:
    """Current month as YYYY-MM (the batch label; independent of send dates)."""
    return datetime.date.today().strftime("%Y-%m")


def manifest_path(run_id: str, base: Path | None = None) -> Path:
    root = base if base is not None else Path(__file__).resolve().parents[1] / "runs"
    return root / run_id / "manifest.json"


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_manifest(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def booklet_default_for_type(type_: str) -> str:
    return _BOOKLET_DEFAULTS.get((type_ or "").strip().lower(), "")


def scaffold_manifest(run_id: str) -> dict:
    """A template manifest with one blank send entry for the operator to fill."""
    return {
        "run_id": run_id,
        "created": datetime.date.today().isoformat(),
        "sheet": {"id": "<SHEET_ID>", "tab": "<SHEET_TAB>"},
        "calendar": {"id": "<CALENDAR_ID>", "mark_initials": "JS"},
        "sends": [
            {
                "client": "<Client Name>",
                "season": "Spring",
                "year": "2026",
                "type": "eNL",
                "send_id": "<send id>",
                "booklet_selector": booklet_default_for_type("eNL"),
                "lead_scoring_de": "",
                "hipaa": False,
                "confirm_zero": False,
            }
        ],
    }

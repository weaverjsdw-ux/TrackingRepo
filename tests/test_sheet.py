"""Phase 3 — Sheet write-back + PDF cross-check (BRIEF §2.A §11).

Matching (client row × metric-header column) and the safety cross-check are
tested with a FakeSheet (no Google creds, CI-safe). The live Google Sheets
adapter is structural-only, like the Gmail adapter.

Safety properties under test (operator decisions this round):
  * a metric the overview PDF reports as nonzero but whose file is absent must
    FAIL LOUD, never be written as a silent 0;
  * the three identical-header bounce files are sub-typed by matching their row
    counts to the PDF's Hard/Soft/Block totals;
  * BH derived via the clicks-derive fallback is FLAGGED; BH == 0 fails loud.
"""

import pytest

from tracking import overview, pipeline
from tracking.sheet import SheetError, build_sheet_plan, write_send

HEAD = ["Client", "Total Sent", "Unique Opens", "Unique Clicks",
        "Hard Bounces", "Soft Bounces", "Block Bounces", "Unsubscribes", "BH"]


class FakeSheet:
    def __init__(self, grid):
        self.grid = [row[:] for row in grid]

    def get_values(self):
        return [row[:] for row in self.grid]

    def update_cell(self, row, col, value):
        self.grid[row][col] = str(value)


def _grid_with_client(client):
    return [HEAD[:], ["Other College"] + [""] * (len(HEAD) - 1),
            [client] + [""] * (len(HEAD) - 1)]


def test_parse_overview_summary(synthetic_send):
    pdf = synthetic_send / "Job_770001_Overview_20260901.pdf"
    s = overview.parse_summary(pdf)
    assert s["Total Sent"] == 6
    assert s["Unique Opens"] == 4
    assert s["Unique Clicks"] == 9
    assert s["Hard Bounces"] == 2
    assert s["Unsubscribes"] == 1


def test_build_plan_subtypes_bounces_and_resolves_metrics(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    plan = build_sheet_plan(result, summary)
    assert plan.values["Total Sent"] == 6
    assert plan.values["Unique Opens"] == 4
    assert plan.values["Hard Bounces"] == 2  # sub-typed via the PDF
    assert plan.values["BH"] == 3


def test_write_send_matches_client_row_and_header_columns(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    plan = build_sheet_plan(result, summary)
    sheet = FakeSheet(_grid_with_client("Northshore College"))

    write_send(sheet, result.identity, plan)

    written = sheet.get_values()[2]  # the Northshore row
    assert written[HEAD.index("Total Sent")] == "6"
    assert written[HEAD.index("Hard Bounces")] == "2"
    assert written[HEAD.index("BH")] == "3"
    # The "Other College" row is untouched.
    assert sheet.get_values()[1][HEAD.index("Total Sent")] == ""


def test_missing_but_expected_metric_fails_loud(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    # PDF claims 99 Soft Bounces but the send provided no Soft Bounce file.
    summary["Soft Bounces"] = 99
    with pytest.raises(SheetError, match="Soft Bounces"):
        build_sheet_plan(result, summary)


def test_bh_fallback_is_flagged(synthetic_send, tmp_path):
    # A send with the master clicks file but no request file -> clicks-derive.
    import shutil
    src = synthetic_send
    drop = tmp_path / "Northshore College - Fall 2026 eNL"
    drop.mkdir()
    for name in ["export_1001.csv", "export_1002.csv", "export_1003.csv",
                 "export_1005.csv", "export_1006.csv", "Job_770001_Overview_20260901.pdf"]:
        shutil.copy(src / name, drop / name)  # note: export_1004 (request file) omitted
    result = pipeline.process_folder(drop)
    summary = overview.parse_summary(drop / "Job_770001_Overview_20260901.pdf")
    plan = build_sheet_plan(result, summary)
    assert result.bh.method.startswith("clicks-derive")
    assert any("fallback" in f.lower() for f in plan.flags)


def test_client_row_not_found_fails_loud(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    plan = build_sheet_plan(result, summary)
    sheet = FakeSheet(_grid_with_client("Totally Different College"))
    with pytest.raises(SheetError, match="row"):
        write_send(sheet, result.identity, plan)

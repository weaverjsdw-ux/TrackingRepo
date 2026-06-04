"""Phase 3 — Sheet write-back + PDF cross-check (BRIEF §2.A §11).

Mapped to the live '2026 Print Status Report' columns. Matching (Client+Type,
season tiebreak) and the safety cross-check are tested with a FakeSheet (no
creds, CI-safe). The live Google Sheets adapter is structural-only."""

import pytest

from tracking import overview, pipeline
from tracking.sheet import SheetError, build_sheet_plan, write_send

# Real header labels (row index 2 on the live sheet). Client in col B(=1).
# 'Unique\nopen rate %' intentionally carries a newline (as the live sheet does)
# to exercise whitespace-insensitive header matching.
HEAD = ["", "Client", "Type", "Issue #",
        "# Total sent", "# Delivered", "# Unique clicks",
        "Booklet landing page unique clicks", "# Total opens", "# Unique opens",
        "Unique\nopen rate %", "Unique click-through %", "Subject line",
        "Actual ... send date"]
BANNER = [""] * len(HEAD)


class FakeSheet:
    def __init__(self, grid):
        self.grid = [row[:] for row in grid]

    def get_values(self):
        return [row[:] for row in self.grid]

    def update_cell(self, row, col, value):
        while len(self.grid[row]) <= col:
            self.grid[row].append("")
        self.grid[row][col] = str(value)


def _row(client, type_, send_date=""):
    r = [""] * len(HEAD)
    r[1], r[2] = client, type_
    r[HEAD.index("Actual ... send date")] = send_date
    return r


def _grid(*data_rows):
    # rows 0,1 banners; row 2 headers; then data.
    return [BANNER[:], BANNER[:], HEAD[:], *data_rows]


def _plan(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    return result, build_sheet_plan(result, summary)


def test_parse_overview_summary(synthetic_send):
    s = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    assert s["Total Sent"] == 6 and s["Unique Opens"] == 4 and s["Unique Clicks"] == 9
    assert s["Delivered"] == 4 and s["Total Opens"] == 5
    assert s["Subject"] == "Northshore Fall Newsletter"


def test_build_plan_maps_to_sheet_columns(synthetic_send):
    _, plan = _plan(synthetic_send)
    assert plan.values == {
        "# Total sent": 6,
        "# Delivered": 4,
        "# Unique clicks": 9,
        "Booklet landing page unique clicks": 3,
        "# Total opens": 5,
        "# Unique opens": 4,
        "Unique open rate %": 1.0,            # 4 unique opens / 4 delivered
        "Unique click-through %": 2.25,       # 9 unique clicks / 4 delivered (synthetic)
        "Subject line": "Northshore Fall Newsletter",
    }


def test_write_send_matches_client_and_type(synthetic_send):
    _, plan = _plan(synthetic_send)
    sheet = FakeSheet(_grid(
        _row("Other College", "eNL"),
        _row("Northshore College", "eQC"),       # right client, wrong type
        _row("Northshore College", "eNL"),       # <- target
    ))
    write_send(sheet, pipeline.process_folder(synthetic_send).identity, plan)
    target = sheet.get_values()[5]               # row idx 5 (3 header + 2 data above)
    assert target[HEAD.index("# Total sent")] == "6"
    assert target[HEAD.index("Booklet landing page unique clicks")] == "3"
    # eQC row untouched.
    assert sheet.get_values()[4][HEAD.index("# Total sent")] == ""


def test_write_send_season_tiebreak(synthetic_send):
    _, plan = _plan(synthetic_send)  # identity season = Fall
    sheet = FakeSheet(_grid(
        _row("Northshore College", "eNL", send_date="4/13/2026"),   # Spring
        _row("Northshore College", "eNL", send_date="10/2/2026"),   # Fall <- target
    ))
    write_send(sheet, pipeline.process_folder(synthetic_send).identity, plan)
    assert sheet.get_values()[4][HEAD.index("# Total sent")] == "6"   # Fall row written
    assert sheet.get_values()[3][HEAD.index("# Total sent")] == ""    # Spring row untouched


def test_ambiguous_rows_fail_loud(synthetic_send):
    _, plan = _plan(synthetic_send)
    sheet = FakeSheet(_grid(
        _row("Northshore College", "eNL", send_date="10/2/2026"),
        _row("Northshore College", "eNL", send_date="10/9/2026"),   # two Fall rows
    ))
    with pytest.raises(SheetError, match="[Aa]mbiguous"):
        write_send(sheet, pipeline.process_folder(synthetic_send).identity, plan)


def test_fill_blanks_only_does_not_overwrite(synthetic_send):
    _, plan = _plan(synthetic_send)
    row = _row("Northshore College", "eNL")
    row[HEAD.index("# Total sent")] = "999"      # pre-existing value
    sheet = FakeSheet(_grid(row))
    write_send(sheet, pipeline.process_folder(synthetic_send).identity, plan)
    assert sheet.get_values()[3][HEAD.index("# Total sent")] == "999"   # preserved
    assert any("already holds" in w for w in plan.warnings)
    assert sheet.get_values()[3][HEAD.index("# Unique opens")] == "4"   # blank got filled


def test_missing_but_expected_metric_fails_loud(synthetic_send):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    del result.metrics["Unique Opens"]           # simulate a forgotten Opens export
    with pytest.raises(SheetError, match="Unique Opens"):
        build_sheet_plan(result, summary)


def test_client_row_not_found_fails_loud(synthetic_send):
    _, plan = _plan(synthetic_send)
    sheet = FakeSheet(_grid(_row("Totally Different College", "eNL")))
    with pytest.raises(SheetError, match="No row"):
        write_send(sheet, pipeline.process_folder(synthetic_send).identity, plan)

"""Golden regression against the REAL (corrected, single-send) Bradley example
folder (BRIEF §1.1).

The folder is git-ignored and lives outside the repo; these tests skip when it
is absent (CI), so no PII is ever required to run the suite. It contains the
FINISHED files and the RAW exports of ONE clean send side by side; the raw
exports were pulled slightly later, so raw vs finished counts differ by a few
rows -- each file is asserted against its OWN count (never forced raw == finished).
"""

import pytest

from tracking import bh
from tracking.identify import FileType, identify
from tracking.parse import link_counts, row_count

pytestmark = pytest.mark.realdata

PREFIX = "Bradley University Spring 2026 eNL"

# --- RAW exports (uninformative names): identity by content + own row count ---
RAW = {
    "export_27241256.csv": (FileType.TOTAL_SENT, 8272),
    "export_27241257.csv": (FileType.BOUNCE, 68),
    "export_27241258.csv": (FileType.BOUNCE, 546),
    "export_27241263.csv": (FileType.BOUNCE, 466),
    "export_27241280.csv": (FileType.UNSUBSCRIBES, 32),
    "export_27241338.csv": (FileType.UNIQUE_CLICKS, 180),
    "export_27241344.csv": (FileType.UNIQUE_OPENS, 2146),
}

# --- FINISHED files: identity by content + own row count ---
FINISHED = {
    f"{PREFIX} - Total Sent.csv": (FileType.TOTAL_SENT, 8272),
    f"{PREFIX} - Unique Opens.csv": (FileType.UNIQUE_OPENS, 2138),
    f"{PREFIX} - Unique Clicks.csv": (FileType.UNIQUE_CLICKS, 179),
    f"{PREFIX} - Request Your.csv": (FileType.BOOKLET_CLICKS, 21),
    f"{PREFIX} - Hard Bounces.csv": (FileType.BOUNCE, 68),
    f"{PREFIX} - Soft Bounces.csv": (FileType.BOUNCE, 546),
    f"{PREFIX} - Block Bounces.csv": (FileType.BOUNCE, 466),
    f"{PREFIX} - Unsubscribes.csv": (FileType.UNSUBSCRIBES, 29),
}


@pytest.mark.parametrize("fname,expected", {**RAW, **FINISHED}.items())
def test_identification_and_counts(real_send, fname, expected):
    exp_type, exp_count = expected
    assert identify(real_send / fname).type is exp_type
    assert row_count(real_send / fname) == exp_count


def test_overview_pdf_identified_by_content(real_send):
    assert identify(real_send / "Job_689524_Overview_06012026.pdf").type is FileType.OVERVIEW_PDF


def test_lead_scoring_identified_so_it_can_be_skipped(real_send):
    assert identify(real_send / "sd_Bradley University - Lead Scoring20260601.csv").type \
        is FileType.LEAD_SCORING


def test_bh_finished_via_request_file_is_21(real_send):
    # PRIMARY path: request file present -> BH = its row count.
    request_count = row_count(real_send / f"{PREFIX} - Request Your.csv")
    master = link_counts(real_send / f"{PREFIX} - Unique Clicks.csv")
    result = bh.resolve_bh(request_count, master_link_counts=master)
    assert result.bh == 21
    assert result.method == "request-file"
    assert result.warning is None  # request (21) agrees with derive (21)


def test_bh_raw_via_clicks_derive_is_21(real_send):
    # FALLBACK path: raw set has NO request file -> derive from the master.
    result = bh.resolve_bh(None, master_link_counts=link_counts(real_send / "export_27241338.csv"))
    assert result.bh == 21
    assert result.method == "clicks-derive(common-parent)"


def test_overview_pdf_summary_parses_real_layout(real_send):
    from tracking import overview
    s = overview.parse_summary(real_send / "Job_689524_Overview_06012026.pdf")
    assert s == {
        "Total Sent": 8272,
        "Hard Bounces": 68,
        "Soft Bounces": 546,
        "Block Bounces": 466,
        "Unique Opens": 2146,
        "Unique Clicks": 180,
        "Unsubscribes": 32,
        "Delivered": 7192,
        "Total Opens": 3665,
        "Subject": "Giving Thought - Spring 2026",
    }

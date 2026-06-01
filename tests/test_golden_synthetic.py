"""End-to-end pipeline golden test on the synthetic send (no PII).

Locks the full identify -> count -> name -> BH result so any future change that
misidentifies, miscounts, misnames, or miscomputes BH fails immediately
(BRIEF §1.1, §6)."""

from tracking import pipeline
from tracking.identify import FileType


def test_process_synthetic_folder(synthetic_send):
    r = pipeline.process_folder(synthetic_send)

    assert r.identity.prefix == "Northshore College Fall 2026 eNL"

    # Metric values that would go into the Sheet.
    assert r.metrics == {
        "Total Sent": 6,
        "Unique Opens": 4,
        "Unique Clicks": 9,
        "Unsubscribes": 1,
        "BH": 3,
    }

    # BH chosen the landing page by common-parent tiebreak.
    assert r.bh is not None and r.bh.bh == 3

    by_type = {p.type: p for p in r.planned}

    # Deterministic finished names.
    assert by_type[FileType.TOTAL_SENT].finished_name == \
        "Northshore College Fall 2026 eNL - Total Sent.csv"
    assert by_type[FileType.UNIQUE_OPENS].finished_name == \
        "Northshore College Fall 2026 eNL - Unique Opens.csv"
    # Request (booklet) file renamed to the fixed "Request Your" label.
    assert by_type[FileType.BOOKLET_CLICKS].finished_name == \
        "Northshore College Fall 2026 eNL - Request Your.csv"

    # BH via request-file primary (3), which agrees with the derive: no warning.
    assert r.bh.method == "request-file"
    assert not any("WARNING" in line for line in r.log)

    # Lead scoring is OUT of the pipeline: ignored, not planned, not a metric.
    assert FileType.LEAD_SCORING not in by_type
    assert any(p.name.startswith("sd_") for p in r.ignored)

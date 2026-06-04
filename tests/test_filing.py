"""Writing the renamed deliverable files (BRIEF §1.1, §6)."""

from tracking import filing, overview, pipeline


def test_write_renamed_creates_full_set(synthetic_send, tmp_path):
    result = pipeline.process_folder(synthetic_send)
    summary = overview.parse_summary(synthetic_send / "Job_770001_Overview_20260901.pdf")
    out = tmp_path / "out"
    written = filing.write_renamed(result, summary, out)

    expected = {
        "Northshore College Fall 2026 eNL - Total Sent.csv",
        "Northshore College Fall 2026 eNL - Unique Opens.csv",
        "Northshore College Fall 2026 eNL - Unique Clicks.csv",
        "Northshore College Fall 2026 eNL - Hard Bounces.csv",   # sub-typed via PDF
        "Northshore College Fall 2026 eNL - Unsubscribes.csv",
        "Northshore College Fall 2026 eNL - Request Your.csv",
        "Northshore College Fall 2026 eNL - Engagement Tracking Report.pdf",
    }
    assert set(written) == expected
    for name in expected:
        assert (out / name).is_file()
    # lead-scoring sd_ file (if present) is not renamed/included
    assert not any("Lead Scoring" in n for n in written)

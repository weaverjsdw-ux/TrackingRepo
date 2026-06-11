"""Run-state tracking for pending sends and operator status."""

from pathlib import Path

from tracking import run_state
from tracking.intake import StagedSend
from tracking.naming import SendIdentity


def _pending(job_id="555111", reason="awaiting overview-PDF email (identity) for this JobID"):
    return StagedSend(
        job_id=job_id,
        drop_folder=Path("drop") / "inbox" / f"job_{job_id}",
        message_ids=[f"m-{job_id}"],
        pending_reason=reason,
    )


def _processed(job_id="555222"):
    return StagedSend(
        job_id=job_id,
        drop_folder=Path("drop") / "processed" / "Northshore College - Fall 2026 eNL",
        message_ids=[f"m-{job_id}"],
        identity=SendIdentity("Northshore College", "Fall", "2026", "eNL"),
    )


def test_record_staged_suppresses_repeated_unchanged_pending(tmp_path):
    state_file = tmp_path / "automation_state.json"

    first = run_state.record_staged(state_file, [_pending()], now="2026-06-11T13:00:00")
    assert [p.job_id for p in first.changed_pending] == ["555111"]
    assert first.unchanged_pending_count == 0

    second = run_state.record_staged(state_file, [_pending()], now="2026-06-11T13:04:00")
    assert second.changed_pending == []
    assert second.unchanged_pending_count == 1

    saved = run_state.load_state(state_file)
    assert saved["pending"]["555111"]["first_seen"] == "2026-06-11T13:00:00"
    assert saved["pending"]["555111"]["last_seen"] == "2026-06-11T13:04:00"
    assert saved["pending"]["555111"]["seen_count"] == 2


def test_record_staged_reports_changed_pending_reason(tmp_path):
    state_file = tmp_path / "automation_state.json"

    run_state.record_staged(state_file, [_pending(reason="awaiting overview")],
                            now="2026-06-11T13:00:00")
    update = run_state.record_staged(state_file, [_pending(reason="processing error: bad pdf")],
                                     now="2026-06-11T13:04:00")

    assert [p.job_id for p in update.changed_pending] == ["555111"]
    saved = run_state.load_state(state_file)
    assert saved["pending"]["555111"]["reason"] == "processing error: bad pdf"


def test_record_staged_moves_completed_job_out_of_pending(tmp_path):
    state_file = tmp_path / "automation_state.json"

    run_state.record_staged(state_file, [_pending(job_id="555222")],
                            now="2026-06-11T13:00:00")
    update = run_state.record_staged(state_file, [_processed(job_id="555222")],
                                     now="2026-06-11T13:04:00")

    saved = run_state.load_state(state_file)
    assert "555222" not in saved["pending"]
    assert saved["processed"]["Northshore College - Fall 2026 eNL"]["job_id"] == "555222"
    assert update.completed_count == 1


def test_format_status_shows_pending_and_processed(tmp_path):
    state_file = tmp_path / "automation_state.json"
    run_state.record_staged(
        state_file,
        [_pending(job_id="555111"), _processed(job_id="555222")],
        now="2026-06-11T13:00:00",
    )

    text = run_state.format_status(state_file)

    assert "Last run: 2026-06-11T13:00:00" in text
    assert "Pending sends: 1" in text
    assert "job 555111" in text
    assert "awaiting overview-PDF" in text
    assert "Processed sends: 1" in text
    assert "Northshore College - Fall 2026 eNL" in text


def test_format_status_includes_processed_folders_from_disk(tmp_path):
    state_file = tmp_path / "automation_state.json"
    processed = tmp_path / "processed"
    (processed / "Northshore College - Fall 2026 eNL").mkdir(parents=True)

    text = run_state.format_status(state_file, processed_root=processed)

    assert "Processed sends: 1" in text
    assert "Northshore College - Fall 2026 eNL: processed folder present" in text

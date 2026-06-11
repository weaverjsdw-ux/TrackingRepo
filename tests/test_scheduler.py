"""Scheduler wrapper behavior.

The PowerShell wrapper is the process Task Scheduler/Power Automate sees, so it
must propagate the Python CLI exit code instead of only writing errors to a log.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess

import pytest


def _powershell():
    if os.name != "nt":
        pytest.skip("PowerShell scheduler wrapper is Windows-only")
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        pytest.skip("PowerShell is not available")
    return exe


def _copy_runner(tmp_path):
    from conftest import REPO_ROOT

    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    source = REPO_ROOT / "scripts" / "run_scheduled.ps1"
    shutil.copy(source, scripts / "run_scheduled.ps1")
    return scripts / "run_scheduled.ps1"


def _cscript():
    if os.name != "nt":
        pytest.skip("Windows Script Host wrapper is Windows-only")
    exe = shutil.which("cscript")
    if exe is None:
        pytest.skip("cscript is not available")
    return exe


def _copy_hidden_runner(tmp_path):
    from conftest import REPO_ROOT

    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    source = REPO_ROOT / "scripts" / "run_scheduled_hidden.vbs"
    shutil.copy(source, scripts / "run_scheduled_hidden.vbs")
    return scripts / "run_scheduled_hidden.vbs"


def test_run_scheduled_returns_python_exit_code(tmp_path):
    runner = _copy_runner(tmp_path)
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "echo fake cli failed\r\n"
        "exit /b 17\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["TRACKING_PYTHON_EXE"] = str(fake_python)

    proc = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 17
    log = tmp_path / "repo" / "logs" / "run.log"
    assert "fake cli failed" in log.read_text(encoding="utf-8")


def test_run_scheduled_ignores_invalid_log_cap(tmp_path):
    runner = _copy_runner(tmp_path)
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "echo fake cli ok\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["TRACKING_PYTHON_EXE"] = str(fake_python)
    env["TRACKING_RUN_LOG_MAX_BYTES"] = "not-a-number"

    proc = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 0
    log = tmp_path / "repo" / "logs" / "run.log"
    text = log.read_text(encoding="utf-8")
    assert "WARN: invalid TRACKING_RUN_LOG_MAX_BYTES" in text
    assert "fake cli ok" in text


def test_run_scheduled_writes_status_json_snapshot_and_preserves_run_exit(tmp_path):
    runner = _copy_runner(tmp_path)
    fake_python = tmp_path / "fake-python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "if \"%3\"==\"status\" (\r\n"
        "  echo {\"pending_count\":4,\"draft_readiness\":{\"state\":\"blocked\"}}\r\n"
        "  exit /b 0\r\n"
        ")\r\n"
        "echo fake cli failed\r\n"
        "exit /b 17\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["TRACKING_PYTHON_EXE"] = str(fake_python)

    proc = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 17
    status = json.loads((tmp_path / "repo" / "logs" / "status.json").read_text(encoding="utf-8-sig"))
    assert status["pending_count"] == 4
    assert status["draft_readiness"]["state"] == "blocked"


def test_hidden_runner_returns_powershell_exit_code(tmp_path):
    hidden_runner = _copy_hidden_runner(tmp_path)
    ps1 = hidden_runner.parent / "run_scheduled.ps1"
    ps1.write_text("exit 17\r\n", encoding="ascii")

    proc = subprocess.run(
        [_cscript(), "//NoLogo", str(hidden_runner)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 17

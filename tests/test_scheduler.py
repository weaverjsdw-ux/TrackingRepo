"""Scheduler wrapper behavior.

The PowerShell wrapper is the process Task Scheduler/Power Automate sees, so it
must propagate the Python CLI exit code instead of only writing errors to a log.
"""

from __future__ import annotations

import os
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

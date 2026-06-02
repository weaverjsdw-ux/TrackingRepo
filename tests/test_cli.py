"""CLI wiring smoke tests (no Gmail/Sheets creds, no network).

The credential-dependent commands (authorize/pull, and write --commit) need live
creds and are exercised manually; here we cover argument wiring, the .env loader,
and the early-return write path that touches no Google API."""

import pytest

from tracking import cli


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nGMAIL_LABEL=my-label\nDROP_ROOT=./drop\n", encoding="utf-8")
    monkeypatch.delenv("GMAIL_LABEL", raising=False)
    cli._load_dotenv(str(env))
    import os
    assert os.environ["GMAIL_LABEL"] == "my-label"


def test_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_write_with_no_processed_sends_is_noop(tmp_path, monkeypatch, capsys):
    # No processed/ dir -> returns 0 without ever touching the Google API.
    monkeypatch.setenv("DROP_ROOT", str(tmp_path))
    rc = cli.main(["write"])
    assert rc == 0
    assert "No processed sends" in capsys.readouterr().out

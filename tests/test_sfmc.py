"""SFMC/ExactTarget API feasibility gate."""

import os

import pytest

from tracking import sfmc
from tracking.naming import SendIdentity


class FakeProbeClient:
    def __init__(self, *, send=True, rows=None, pdf=True):
        self.send = send
        self.rows = rows or {"sent": 1, "open": 1, "click": 1, "bounce": 1, "unsub": 1}
        self.pdf = pdf
        self.calls = []

    def authenticate(self):
        self.calls.append(("authenticate",))
        return True

    def find_send(self, send_id):
        self.calls.append(("find_send", send_id))
        return self.send

    def tracking_count(self, send_id, metric):
        self.calls.append(("tracking_count", send_id, metric))
        return self.rows.get(metric, 0)

    def overview_pdf_available(self, send_id):
        self.calls.append(("overview_pdf_available", send_id))
        return self.pdf


def test_probe_checks_auth_send_tracking_and_pdf():
    client = FakeProbeClient()

    result = sfmc.probe_capabilities(client, "12345")

    assert result.ok is True
    assert result.requires_ui_fallback is False
    assert ("tracking_count", "12345", "click") in client.calls
    assert "overview PDF: available" in sfmc.format_probe_result(result)


def test_probe_requires_ui_fallback_when_official_pdf_unavailable():
    result = sfmc.probe_capabilities(FakeProbeClient(pdf=False), "12345")

    assert result.ok is False
    assert result.requires_ui_fallback is True
    assert "official overview PDF unavailable" in result.blockers


def test_probe_fails_when_send_cannot_be_found():
    result = sfmc.probe_capabilities(FakeProbeClient(send=False), "12345")

    assert result.ok is False
    assert "send lookup failed" in result.blockers


def test_stage_artifacts_writes_canonical_folder(tmp_path):
    identity = SendIdentity("Northshore College", "Fall", "2026", "eNL")
    folder = sfmc.stage_artifacts(
        tmp_path,
        identity,
        [sfmc.SfmcArtifact("export_1001.csv", b"Email Address\nperson@example.com\n")],
    )

    assert folder == tmp_path / "sfmc" / "Northshore College - Fall 2026 eNL"
    assert (folder / "export_1001.csv").read_bytes().startswith(b"Email Address")


def test_stage_send_fetches_artifacts_from_source_adapter(tmp_path):
    class FakeArtifactClient:
        def __init__(self):
            self.send_ids = []

        def fetch_artifacts(self, send_id):
            self.send_ids.append(send_id)
            return [sfmc.SfmcArtifact("export_sent.csv", b"sent")]

    client = FakeArtifactClient()
    identity = SendIdentity("Northshore College", "Fall", "2026", "eNL")

    folder = sfmc.stage_send(tmp_path, identity, client, "12345")

    assert client.send_ids == ["12345"]
    assert (folder / "export_sent.csv").read_text(encoding="utf-8") == "sent"


def test_real_client_from_env_requires_credentials(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SFMC_"):
            monkeypatch.delenv(key, raising=False)

    with pytest.raises(sfmc.SfmcConfigError, match="SFMC_AUTH_BASE_URL"):
        sfmc.RealSfmcClient.from_env()

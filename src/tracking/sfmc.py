"""Salesforce Marketing Cloud / ExactTarget API feasibility gate.

The only universal SFMC REST detail this module assumes is the OAuth v2 token
flow. Send lookup, tracking rows, and official overview PDF retrieval are probed
through explicit URL templates because those capabilities depend on installed
package permissions and the account's available APIs.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .naming import SendIdentity
from .pipeline import assess_completeness, process_folder

TRACKING_METRICS = ("sent", "open", "click", "bounce", "unsub")
REQUIRED_ARTIFACTS = ("sent", "open", "click", "bounce", "unsub", "overview_pdf")
OPTIONAL_ARTIFACTS = ("booklet",)


class SfmcConfigError(ValueError):
    """Raised when SFMC probing is not configured enough to run safely."""


@dataclass(frozen=True)
class SfmcArtifact:
    filename: str
    data: bytes


@dataclass
class SfmcProbeResult:
    send_id: str
    checks: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    tracking_counts: dict[str, int] = field(default_factory=dict)
    requires_ui_fallback: bool = False

    @property
    def ok(self) -> bool:
        return not self.blockers and not self.requires_ui_fallback


@runtime_checkable
class SfmcProbeClient(Protocol):
    def authenticate(self) -> bool: ...
    def find_send(self, send_id: str) -> bool: ...
    def tracking_count(self, send_id: str, metric: str) -> int: ...
    def overview_pdf_available(self, send_id: str) -> bool: ...


@runtime_checkable
class SfmcArtifactClient(Protocol):
    def fetch_artifacts(self, send_id: str) -> list[SfmcArtifact]: ...


def probe_capabilities(client: SfmcProbeClient, send_id: str) -> SfmcProbeResult:
    result = SfmcProbeResult(send_id=send_id)
    try:
        client.authenticate()
        result.checks.append("auth: ok")
    except Exception as exc:  # noqa: BLE001 - probe reports, not raises
        result.blockers.append(f"auth failed: {exc}")
        return result

    try:
        if not client.find_send(send_id):
            result.blockers.append("send lookup failed")
            return result
        result.checks.append("send lookup: ok")
    except Exception as exc:  # noqa: BLE001
        result.blockers.append(f"send lookup failed: {exc}")
        return result

    for metric in TRACKING_METRICS:
        try:
            result.tracking_counts[metric] = client.tracking_count(send_id, metric)
            result.checks.append(f"{metric} tracking: ok")
        except Exception as exc:  # noqa: BLE001
            result.blockers.append(f"{metric} tracking unavailable: {exc}")

    try:
        if client.overview_pdf_available(send_id):
            result.checks.append("overview PDF: available")
        else:
            result.requires_ui_fallback = True
            result.blockers.append("official overview PDF unavailable")
    except Exception as exc:  # noqa: BLE001
        result.requires_ui_fallback = True
        result.blockers.append(f"official overview PDF unavailable: {exc}")
    return result


def format_probe_result(result: SfmcProbeResult) -> str:
    lines = [f"SFMC probe for send {result.send_id}: {'OK' if result.ok else 'BLOCKED'}"]
    lines.extend(f"  {line}" for line in result.checks)
    for metric, count in sorted(result.tracking_counts.items()):
        lines.append(f"  {metric} rows: {count}")
    lines.extend(f"  BLOCKER: {line}" for line in result.blockers)
    if result.requires_ui_fallback:
        lines.append("  Fallback: use UI/Power Automate only for missing official artifacts.")
    return "\n".join(lines)


def stage_artifacts(
    drop_root: str | Path,
    identity: SendIdentity,
    artifacts: list[SfmcArtifact],
) -> Path:
    """Stage a complete SFMC artifact set into the canonical processed folder."""
    root = Path(drop_root)
    folder = root / "sfmc" / identity.folder_name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        (folder / artifact.filename).write_bytes(artifact.data)

    try:
        result = process_folder(folder, identity)
    except Exception as exc:  # noqa: BLE001 - operator-facing feasibility gate
        raise SfmcConfigError(f"incomplete SFMC artifact set: {exc}") from exc
    ok, missing = assess_completeness(result)
    if not ok:
        raise SfmcConfigError(f"incomplete SFMC artifact set: missing {missing}")

    processed = root / "processed" / identity.folder_name
    if processed.exists():
        shutil.rmtree(processed)
    processed.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(processed))
    return processed


def stage_send(
    drop_root: str | Path,
    identity: SendIdentity,
    client: SfmcArtifactClient,
    send_id: str,
) -> Path:
    """Fetch API artifacts and stage them as a normal processable send folder."""
    return stage_artifacts(drop_root, identity, client.fetch_artifacts(send_id))


class RealSfmcClient:
    def __init__(
        self,
        *,
        auth_base_url: str,
        client_id: str,
        client_secret: str,
        account_id: str | None = None,
        rest_base_url: str | None = None,
        url_templates: dict[str, str] | None = None,
    ):
        self.auth_base_url = auth_base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_id = account_id
        self.rest_base_url = rest_base_url.rstrip("/") if rest_base_url else None
        self.url_templates = url_templates or {}
        self._token: str | None = None

    @classmethod
    def from_env(cls) -> "RealSfmcClient":
        required = ["SFMC_AUTH_BASE_URL", "SFMC_CLIENT_ID", "SFMC_CLIENT_SECRET"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise SfmcConfigError(f"Missing SFMC configuration: {', '.join(missing)}")
        templates = {
            "send": os.environ.get("SFMC_SEND_LOOKUP_URL", ""),
            "overview_pdf": os.environ.get("SFMC_OVERVIEW_PDF_URL", ""),
        }
        for metric in TRACKING_METRICS:
            templates[metric] = os.environ.get(f"SFMC_TRACKING_{metric.upper()}_URL", "")
        for artifact in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
            templates[f"artifact_{artifact}"] = os.environ.get(
                f"SFMC_ARTIFACT_{artifact.upper()}_URL", ""
            )
        return cls(
            auth_base_url=os.environ["SFMC_AUTH_BASE_URL"],
            client_id=os.environ["SFMC_CLIENT_ID"],
            client_secret=os.environ["SFMC_CLIENT_SECRET"],
            account_id=os.environ.get("SFMC_ACCOUNT_ID"),
            rest_base_url=os.environ.get("SFMC_REST_BASE_URL"),
            url_templates=templates,
        )

    def authenticate(self) -> bool:
        body = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.account_id:
            body["account_id"] = self.account_id
        data = self._request_json(f"{self.auth_base_url}/v2/token", method="POST", body=body)
        self._token = data["access_token"]
        if data.get("rest_instance_url") and not self.rest_base_url:
            self.rest_base_url = str(data["rest_instance_url"]).rstrip("/")
        return True

    def find_send(self, send_id: str) -> bool:
        template = self._template("send")
        try:
            self._request_json(self._url(template, send_id))
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def tracking_count(self, send_id: str, metric: str) -> int:
        template = self._template(metric)
        data = self._request_json(self._url(template, send_id))
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("count", "totalCount", "total", "rowCount"):
                if key in data:
                    return int(data[key])
            for key in ("items", "rows", "data"):
                if isinstance(data.get(key), list):
                    return len(data[key])
        raise SfmcConfigError(f"Cannot infer row count from {metric} response.")

    def overview_pdf_available(self, send_id: str) -> bool:
        template = (
            self.url_templates.get("overview_pdf")
            or self.url_templates.get("artifact_overview_pdf")
            or ""
        )
        if not template:
            return False
        data = self._request_bytes(self._url(template, send_id))
        return data.startswith(b"%PDF")

    def fetch_artifacts(self, send_id: str) -> list[SfmcArtifact]:
        if self._token is None:
            self.authenticate()
        missing = [
            artifact for artifact in REQUIRED_ARTIFACTS
            if not self.url_templates.get(f"artifact_{artifact}")
        ]
        if missing:
            raise SfmcConfigError(
                "Missing artifact URL template(s): "
                + ", ".join(f"SFMC_ARTIFACT_{name.upper()}_URL" for name in missing)
            )

        artifacts: list[SfmcArtifact] = []
        for artifact in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
            template = self.url_templates.get(f"artifact_{artifact}") or ""
            if not template:
                continue
            data = self._request_bytes(self._url(template, send_id))
            if artifact == "overview_pdf" and not data.startswith(b"%PDF"):
                raise SfmcConfigError("Official overview PDF artifact is not a PDF.")
            artifacts.append(SfmcArtifact(_artifact_filename(artifact, send_id), data))
        return artifacts

    def _template(self, key: str) -> str:
        template = self.url_templates.get(key) or ""
        if not template:
            raise SfmcConfigError(f"Missing URL template for {key!r}.")
        return template

    def _url(self, template: str, send_id: str) -> str:
        path = template.format(send_id=send_id)
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not self.rest_base_url:
            raise SfmcConfigError("SFMC_REST_BASE_URL is required for relative probe URLs.")
        return f"{self.rest_base_url}/{path.lstrip('/')}"

    def _request_json(self, url: str, *, method: str = "GET", body: dict | None = None):
        raw = self._request_bytes(url, method=method, body=body)
        return json.loads(raw.decode("utf-8"))

    def _request_bytes(self, url: str, *, method: str = "GET", body: dict | None = None) -> bytes:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._token and "/v2/token" not in url:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - operator-configured URL
            return resp.read()


def _artifact_filename(kind: str, send_id: str) -> str:
    if kind == "overview_pdf":
        return f"Job_{send_id}_Overview.pdf"
    return f"sfmc_{kind}_{send_id}.csv"

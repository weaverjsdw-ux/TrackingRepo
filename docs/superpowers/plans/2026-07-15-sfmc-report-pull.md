# SFMC Report Pull (`pull_reports`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one saved, manifest-driven CLI (`scripts/pull_reports.py`) that pulls a selected SFMC send and produces its full report package — engagement CSVs, styled PDF, Lead Scoring export, Print Status Report row, calendar mark, and Gmail drafts — with all per-run facts living in `runs/<run-id>/manifest.json` and none baked into code.

**Architecture:** A single script with clearly-sectioned, importable pure functions (manifest I/O, SOAP envelope/parse, metric transforms, CSV/sheet/calendar/draft planning) plus thin network/IO adapters. Pure logic is unit-tested offline with synthetic fixtures and injectable transports; the script imports the package's proven `tracking.naming`, `tracking.sheet`, `tracking.sheets_writer`, `tracking.gmail_source`, and `tracking.drafts` helpers rather than re-implementing them. The SFMC client is verbatim-ported from the approved, working scratchpad scripts, with the `%TEMP%\sfmc_plans.json` side-channel removed (all PDF/sheet values computed live from the `Send` object + events).

**Tech Stack:** Python ≥3.11, `urllib` (SFMC SOAP 1.1 + REST), `reportlab` (PDF), `google-api-python-client`/`google-auth` (Sheets + Calendar via `GoogleSheetsWriter`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-15-sfmc-report-pull-design.md` (approved 2026-07-15).

## Global Constraints

- **Rules live in code; facts live in the manifest.** No month, client, SendID, booklet tag, count, or folder list is hardcoded. (Spec §2)
- **`.gitignore` hardening lands before any command that can create `runs/` or write CSVs.** (Spec §7, sequencing constraint — this is Task 1.)
- **Fill-blank-only** for sheet + calendar writes unless `--force`. (Spec Rule 5)
- **Gmail creates drafts only, never sends.** (Spec Rule 6)
- **HIPAA is required per send**; absent → the send fails loud; `true` → Kathryn notification skipped + flagged, export still runs. (Spec Rule 7, §5.4)
- **Zero-core gate:** Total Sent = 0 → `status: failed`; zero Unique Opens or Unique Clicks → `status: needs_confirmation`, which **blocks sheet/calendar/drafts** for that send until `confirm_zero: true` or `--confirm-zero <send_id>`. Local CSVs/PDF still write. (Spec Rule 3, §5.2)
- **`--dry-run` mutates nothing** — no files, drafts, sheet/calendar writes, or per-send manifest results. (Spec Rule 8, §4)
- **Loud failure, per-send isolation:** a failing send records `status: failed` + `errors[]` and the batch continues. Never write a wrong/placeholder number. (Spec §6)
- **SubscriberKey == Email Address** in this account; no profile join. (Spec §5.2)
- **Verbatim Lead Scoring name** `<resolved DE name><YYYYMMDD>.csv`, never renamed. (Spec Rule 4)
- **SOAP is 1.1** (not 1.2/WS-Addressing): `xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"`, token in `<fueloauth xmlns="http://exacttarget.com">`, POST `{soap}/Service.asmx`, `SOAPAction: Retrieve`, page via `ContinueRequest` while `OverallStatus=MoreDataAvailable`. (Spec §5.1)
- **Baseline suite is green (119 passed).** New tests extend it; CI (`pytest -m "not realdata"`) must stay green.
- All commands run with the repo venv: `./.venv/Scripts/python.exe` (Windows).

---

## File Structure

- `scripts/pull_reports.py` — **the tool** (created incrementally, Tasks 2–12). Sections: config/constants · manifest I/O · SFMC client · metric transforms · engagement CSVs · report PDF · sheet mapping · calendar · lead scoring · drafts · orchestration/CLI.
- `tests/test_pull_reports.py` — offline unit tests (created incrementally). Imports the script via a `sys.path` shim.
- `.gitignore` — hardened (Task 1).
- `pyproject.toml` — add `reportlab` as a **core** dependency (Task 1).
- `.env.example` — add SFMC SOAP + calendar + lead-scoring vars (Task 1).
- `runs/example/manifest.example.json` — committed redacted schema (Task 1).
- `README.md`, `docs/AUTOMATION_STATUS.md` — operator docs point at the new tool (Task 13).

Note: `src/tracking/sfmc.py` (legacy REST-probe) is **not touched**.

---

### Task 1: Repo safety, deps, config, example manifest (gitignore-first)

This task must land first: it installs the PII protection before any code can create `runs/` or write CSVs.

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml:15-26` (optional-dependencies)
- Modify: `.env.example`
- Create: `runs/example/manifest.example.json`

- [ ] **Step 1: Harden `.gitignore`**

Add these blocks. The `runs/*` + `!runs/example/` pair ignores every real run dir but keeps the committed example; the eQC line closes the gap where only `eNL`/`ePC` were ignored.

Append to `.gitignore`:

```gitignore
# eQC metric CSVs (belt-and-suspenders, like the eNL/ePC lines above).
* eQC - *.csv

# Per-run manifests hold PII (client names, SendIDs, counts, draft IDs) —
# never commit real runs; keep only the redacted example.
runs/*
!runs/example/
```

- [ ] **Step 2: Verify the ignore rules**

Run in PowerShell (the session's primary shell):

```powershell
git check-ignore -v "runs/2026-07/manifest.json"                    # expect: a matching rule (ignored)
git check-ignore -v "Some Client Spring 2026 eQC - Total Sent.csv"  # expect: a matching rule (ignored)
git check-ignore "runs/example/manifest.example.json"; "exit=$LASTEXITCODE"  # expect: no path printed, exit=1
```
Expected: first two print a matching rule; the third prints nothing and `exit=1` (NOT ignored).

- [ ] **Step 3: Add `reportlab` as a CORE dependency in `pyproject.toml`**

reportlab is a genuine runtime dependency AND is imported at the top of `scripts/pull_reports.py`, so `test_pull_reports.py` needs it whenever the suite runs. CI installs `pip install -e .[dev]` then runs the (non-realdata) PDF smoke test, so an optional extra would not be installed and CI would fail at import. Make it **core**, not an extra.

In `[project]`, add reportlab to `dependencies` (lines 10-13):

```toml
dependencies = [
    "pandas>=2.0",
    "pdfplumber>=0.11",
    "reportlab>=4.0",
]
```

- [ ] **Step 4: Extend `.env.example`**

Append a new section documenting the pull tool's config (the existing SFMC block is probe-oriented; this adds the SOAP + calendar + lead-scoring vars the tool needs):

```bash

# --- pull_reports.py (manifest-driven SFMC report pull) ---
# SOAP base is required for the Send object + tracking-event retrieves.
# SFMC_SOAP_BASE_URL=https://YOURSUBDOMAIN.soap.marketingcloudapis.com
# Print Status Report target (uncomment; same sheet the tool writes rows to).
# SHEET_ID=1oi851C2NUoCLELTw80-tmEL7ztyHM_u-KQDubKM1tnY
# SHEET_TAB=2026 Print Status Report
# GOOGLE_SHEETS_SERVICE_ACCOUNT=secrets/service-account.json
# Tracking Reports calendar (marked fill-blank-only by the same service account).
# CALENDAR_ID=1eTZXc9bNaRbMWmFeJPPc56LCmbO42egvkJy1Sjo89is
# CALENDAR_MARK_INITIALS=JS
# Where Lead Scoring CSVs are written (default: <REPORTS_DIR>/Lead Scoring).
# LEAD_SCORING_DIR=../Lead Scoring
```

- [ ] **Step 5: Create the redacted example manifest**

Create `runs/example/manifest.example.json` (illustrative values only — no real PII):

```json
{
  "run_id": "2026-07",
  "created": "2026-07-15",
  "sheet": { "id": "<SHEET_ID>", "tab": "<SHEET_TAB>" },
  "calendar": { "id": "<CALENDAR_ID>", "mark_initials": "JS" },
  "sends": [
    {
      "client": "Example College",
      "season": "Spring",
      "year": "2026",
      "type": "eNL",
      "send_id": "000000",
      "booklet_selector": "v=enlA",
      "lead_scoring_de": "sd_Example College - Lead Scoring",
      "hipaa": false,
      "confirm_zero": false
    }
  ]
}
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml .env.example runs/example/manifest.example.json
git commit -m "chore: gitignore/deps/env safety + example manifest for pull_reports"
```

---

### Task 2: Script skeleton + manifest I/O

**Files:**
- Create: `scripts/pull_reports.py`
- Create: `tests/test_pull_reports.py`

**Interfaces:**
- Produces: `default_run_id() -> str`; `manifest_path(run_id: str, base: Path|None=None) -> Path`; `load_manifest(path: Path) -> dict`; `save_manifest(path: Path, data: dict) -> None`; `booklet_default_for_type(type_: str) -> str`; `scaffold_manifest(run_id: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pull_reports.py`:

```python
import importlib.util
import sys
from pathlib import Path

# Load scripts/pull_reports.py as an importable module.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pull_reports.py"
_spec = importlib.util.spec_from_file_location("pull_reports", _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
sys.modules["pull_reports"] = pr
_spec.loader.exec_module(pr)


def test_manifest_round_trip(tmp_path):
    p = tmp_path / "manifest.json"
    data = {"run_id": "2026-07", "sends": [{"send_id": "1", "client": "X"}]}
    pr.save_manifest(p, data)
    assert pr.load_manifest(p) == data


def test_default_run_id_is_year_month():
    rid = pr.default_run_id()
    assert len(rid) == 7 and rid[4] == "-"  # YYYY-MM


def test_booklet_default_for_type():
    assert pr.booklet_default_for_type("eNL") == "v=enlA"
    assert pr.booklet_default_for_type("eQC") == "/requestguide"
    assert pr.booklet_default_for_type("ePC") == ""  # ePC has no default; operator supplies cID


def test_scaffold_manifest_prefills_booklet_by_type():
    m = pr.scaffold_manifest("2026-08")
    assert m["run_id"] == "2026-08"
    assert m["sends"] and m["sends"][0]["booklet_selector"] == "v=enlA"
    assert m["sends"][0]["hipaa"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -v`
Expected: collection error / FAIL — `scripts/pull_reports.py` does not exist.

- [ ] **Step 3: Create the script skeleton + manifest functions**

Create `scripts/pull_reports.py`:

```python
"""pull_reports.py — manifest-driven SFMC report pull.

One saved tool that pulls a selected send from Salesforce Marketing Cloud and
produces its full report package (engagement CSVs, styled PDF, Lead Scoring
export, Print Status Report row, calendar mark, Gmail drafts). All per-run
facts live in runs/<run-id>/manifest.json; none are hardcoded.

See docs/superpowers/specs/2026-07-15-sfmc-report-pull-design.md.

Run with the repo venv:  ./.venv/Scripts/python.exe scripts/pull_reports.py <cmd>
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

# Make the installed `tracking` package importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ── booklet selector defaults by send type (a RULE; the value is confirmed
# per send in the manifest). eNL newsletters tag the booklet link v=enlA;
# eQC quarterlies use the /requestguide URL; ePC postcards vary (cID) so no
# default — the operator supplies it.
_BOOKLET_DEFAULTS = {"enl": "v=enlA", "eqc": "/requestguide"}


def default_run_id() -> str:
    """Current month as YYYY-MM (the batch label; independent of send dates)."""
    return datetime.date.today().strftime("%Y-%m")


def manifest_path(run_id: str, base: Path | None = None) -> Path:
    root = base if base is not None else Path(__file__).resolve().parents[1] / "runs"
    return root / run_id / "manifest.json"


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_manifest(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def booklet_default_for_type(type_: str) -> str:
    return _BOOKLET_DEFAULTS.get((type_ or "").strip().lower(), "")


def scaffold_manifest(run_id: str) -> dict:
    """A template manifest with one blank send entry for the operator to fill."""
    return {
        "run_id": run_id,
        "created": datetime.date.today().isoformat(),
        "sheet": {"id": "<SHEET_ID>", "tab": "<SHEET_TAB>"},
        "calendar": {"id": "<CALENDAR_ID>", "mark_initials": "JS"},
        "sends": [
            {
                "client": "<Client Name>",
                "season": "Spring",
                "year": "2026",
                "type": "eNL",
                "send_id": "<send id>",
                "booklet_selector": booklet_default_for_type("eNL"),
                "lead_scoring_de": "",
                "hipaa": False,
                "confirm_zero": False,
            }
        ],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): script skeleton + manifest I/O"
```

---

### Task 3: SOAP envelope + response parsing (pure)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Produces: `local_name(tag: str) -> str`; `build_retrieve_envelope(obj: str, props: list[str], filt: str="", cont: str|None=None, token: str="TOKEN") -> str`; `parse_soap(raw: bytes) -> tuple[str|None, str|None, list[dict]]` returning `(overall_status, request_id, rows)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py`:

```python
_SOAP_OK = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Body>
  <RetrieveResponseMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">
   <OverallStatus>OK</OverallStatus>
   <RequestID>req-1</RequestID>
   <Results xsi:type="Send" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
     <ID>691994</ID><NumberSent>1000</NumberSent>
   </Results>
  </RetrieveResponseMsg>
 </soap:Body>
</soap:Envelope>"""


def test_parse_soap_reads_status_and_rows():
    status, req_id, rows = pr.parse_soap(_SOAP_OK)
    assert status == "OK"
    assert req_id == "req-1"
    assert rows == [{"ID": "691994", "NumberSent": "1000"}]


def test_build_retrieve_envelope_has_soap11_and_token_and_filter():
    env = pr.build_retrieve_envelope(
        "OpenEvent", ["SubscriberKey", "EventDate"],
        filt="<Filter>x</Filter>", token="ABC",
    )
    assert 'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"' in env
    assert '<fueloauth xmlns="http://exacttarget.com">ABC</fueloauth>' in env
    assert "<ObjectType>OpenEvent</ObjectType>" in env
    assert "<Properties>SubscriberKey</Properties>" in env
    assert "<Filter>x</Filter>" in env


def test_build_retrieve_envelope_continue_request():
    env = pr.build_retrieve_envelope("OpenEvent", ["SubscriberKey"], cont="req-1", token="ABC")
    assert "<ContinueRequest>req-1</ContinueRequest>" in env
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "soap or envelope" -v`
Expected: FAIL — `parse_soap`/`build_retrieve_envelope` not defined.

- [ ] **Step 3: Implement (ported verbatim from the working scratchpad SOAP layer)**

Add to `scripts/pull_reports.py` (after imports, add `import xml.etree.ElementTree as ET` to the import block):

```python
import xml.etree.ElementTree as ET


def local_name(tag: str) -> str:
    """Strip any XML namespace: '{ns}Results' -> 'Results'."""
    return tag.rsplit("}", 1)[-1]


def build_retrieve_envelope(obj, props, filt="", cont=None, token="TOKEN"):
    p = "".join(f"<Properties>{x}</Properties>" for x in props)
    if cont:
        inner = f"<ContinueRequest>{cont}</ContinueRequest><ObjectType>{obj}</ObjectType>{p}"
    else:
        inner = f"<ObjectType>{obj}</ObjectType>{p}{filt}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<soapenv:Header><fueloauth xmlns="http://exacttarget.com">{token}</fueloauth></soapenv:Header>'
        '<soapenv:Body><RetrieveRequestMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">'
        f"<RetrieveRequest>{inner}</RetrieveRequest></RetrieveRequestMsg></soapenv:Body></soapenv:Envelope>"
    )


def parse_soap(raw):
    """Return (OverallStatus, RequestID, [row dicts]) from a Retrieve response."""
    root = ET.fromstring(raw)
    status = req_id = None
    rows = []
    for el in root.iter():
        if local_name(el.tag) == "OverallStatus" and status is None:
            status = el.text
        if local_name(el.tag) == "RequestID" and req_id is None:
            req_id = el.text
    for el in root.iter():
        if local_name(el.tag) == "Results":
            rows.append({local_name(c.tag): c.text for c in el})
    return status, req_id, rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "soap or envelope" -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): SOAP 1.1 envelope + response parsing"
```

---

### Task 4: SFMC client (auth, SOAP paging, events, REST rowset)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `build_retrieve_envelope`, `parse_soap`.
- Produces: `class SfmcClient(auth_base, soap_base, rest_base, client_id, client_secret, transport=None)` with `.token`, `.soap_url`, `.rest_url`, and methods `authenticate() -> None`, `retrieve(obj, props, filt="") -> list[dict]` (auto-paging), `get_send(send_id) -> dict`, `get_events(obj, send_id, props) -> list[dict]`, `get_de_rows_rest(customer_key) -> list[dict]`. Transport is an object with `post(url, data: bytes, headers: dict) -> (int, bytes)` and `get(url, headers: dict) -> (int, bytes)`; the default is a urllib wrapper.

- [ ] **Step 1: Write the failing tests (fake transport — no network)**

Append to `tests/test_pull_reports.py`:

```python
def _soap_results(status, req_id, rows, obj="Send"):
    body = ""
    for r in rows:
        cells = "".join(f"<{k}>{v}</{k}>" for k, v in r.items())
        body += f'<Results xsi:type="{obj}">{cells}</Results>'
    rid = f"<RequestID>{req_id}</RequestID>" if req_id else ""
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>'  # xsi declared: fixtures use xsi:type
        '<RetrieveResponseMsg xmlns="http://exacttarget.com/wsdl/partnerAPI">'
        f"<OverallStatus>{status}</OverallStatus>{rid}{body}"
        "</RetrieveResponseMsg></soap:Body></soap:Envelope>"
    ).encode()


class FakeTransport:
    def __init__(self, soap_responses=None, get_responses=None):
        self.soap_responses = list(soap_responses or [])
        self.get_responses = list(get_responses or [])
        self.posts = []
        self.gets = []

    def post(self, url, data, headers):
        self.posts.append((url, data, headers))
        if url.endswith("/v2/token"):
            return 200, b'{"access_token":"TOK","soap_instance_url":"https://s.example.com/","rest_instance_url":"https://r.example.com/"}'
        return 200, self.soap_responses.pop(0)

    def get(self, url, headers):
        self.gets.append((url, headers))
        return 200, self.get_responses.pop(0)


def _client(transport):
    return pr.SfmcClient(
        auth_base="https://auth.example.com", soap_base="https://s.example.com",
        rest_base="https://r.example.com", client_id="id", client_secret="sec",
        transport=transport,
    )


def test_retrieve_pages_via_continue_request():
    t = FakeTransport(soap_responses=[
        _soap_results("MoreDataAvailable", "req-1", [{"SubscriberKey": "a@x"}], obj="OpenEvent"),
        _soap_results("OK", None, [{"SubscriberKey": "b@x"}], obj="OpenEvent"),
    ])
    c = _client(t); c.authenticate()
    rows = c.get_events("OpenEvent", "691994", ["SubscriberKey"])
    assert [r["SubscriberKey"] for r in rows] == ["a@x", "b@x"]
    # Second SOAP post carried a ContinueRequest.
    assert b"<ContinueRequest>req-1</ContinueRequest>" in t.posts[2][1]


def test_get_send_returns_first_row():
    t = FakeTransport(soap_responses=[_soap_results("OK", None, [{"ID": "691994", "NumberSent": "1000"}])])
    c = _client(t); c.authenticate()
    send = c.get_send("691994")
    assert send["NumberSent"] == "1000"


def test_get_de_rows_rest_merges_keys_and_values():
    body = b'{"count":1,"page":1,"pageSize":2500,"items":[{"keys":{"subscriberkey":"a@x"},"values":{"score":"5","donor id":"9"}}]}'
    t = FakeTransport(get_responses=[body])
    c = _client(t); c.authenticate()
    rows = c.get_de_rows_rest("KEY-123")
    assert rows == [{"subscriberkey": "a@x", "score": "5", "donor id": "9"}]
    assert "/data/v1/customobjectdata/key/KEY-123/rowset" in t.gets[0][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "retrieve or get_send or de_rows" -v`
Expected: FAIL — `SfmcClient` not defined.

- [ ] **Step 3: Implement the client**

Add to `scripts/pull_reports.py` (add `import urllib.request, urllib.error` to imports):

```python
import urllib.error
import urllib.request


class _UrllibTransport:
    def post(self, url, data, headers):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=headers, method="POST"), timeout=300
            ) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, url, headers):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers, method="GET"), timeout=300
            ) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class SfmcError(RuntimeError):
    pass


class SfmcClient:
    def __init__(self, *, auth_base, soap_base, rest_base, client_id, client_secret, transport=None):
        self.auth_base = auth_base.rstrip("/")
        self.soap_base = (soap_base or "").rstrip("/")
        self.rest_base = (rest_base or "").rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or _UrllibTransport()
        self.token = None
        self.soap_url = None
        self.rest_url = None

    def authenticate(self):
        body = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        st, raw = self.transport.post(
            f"{self.auth_base}/v2/token", body, {"Content-Type": "application/json"}
        )
        if st != 200:
            raise SfmcError(f"SFMC auth failed (HTTP {st}): {raw[:200]!r}")
        tok = json.loads(raw)
        self.token = tok["access_token"]
        self.soap_url = (tok.get("soap_instance_url") or self.soap_base).rstrip("/")
        self.rest_url = (tok.get("rest_instance_url") or self.rest_base).rstrip("/")

    def _soap_call(self, obj, props, filt="", cont=None):
        env = build_retrieve_envelope(obj, props, filt=filt, cont=cont, token=self.token)
        st, raw = self.transport.post(
            f"{self.soap_url}/Service.asmx", env.encode(),
            {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "Retrieve"},
        )
        if st != 200:
            raise SfmcError(f"SOAP {obj} failed (HTTP {st}): {raw[:200]!r}")
        return parse_soap(raw)

    def retrieve(self, obj, props, filt=""):
        status, req_id, rows = self._soap_call(obj, props, filt=filt)
        guard = 0
        while status == "MoreDataAvailable" and req_id and guard < 2000:
            guard += 1
            status, req_id, more = self._soap_call(obj, props, cont=req_id)
            rows += more
        return rows

    def get_send(self, send_id):
        filt = (
            '<Filter xsi:type="SimpleFilterPart"><Property>ID</Property>'
            f"<SimpleOperator>equals</SimpleOperator><Value>{send_id}</Value></Filter>"
        )
        rows = self.retrieve("Send", [
            "ID", "EmailName", "Subject", "SentDate", "NumberSent", "NumberDelivered",
            "HardBounces", "SoftBounces", "OtherBounces", "UniqueOpens", "UniqueClicks", "Unsubscribes",
        ], filt)
        if not rows:
            raise SfmcError(f"No Send found for ID {send_id}.")
        return rows[0]

    def get_events(self, obj, send_id, props):
        filt = (
            '<Filter xsi:type="SimpleFilterPart"><Property>SendID</Property>'
            f"<SimpleOperator>equals</SimpleOperator><Value>{send_id}</Value></Filter>"
        )
        return self.retrieve(obj, props, filt)

    def get_de_rows_rest(self, customer_key, page_size=2500):
        out = []
        page = 1
        while True:
            url = (f"{self.rest_url}/data/v1/customobjectdata/key/{customer_key}/rowset"
                   f"?$page={page}&$pageSize={page_size}")
            st, raw = self.transport.get(url, {"Authorization": f"Bearer {self.token}"})
            if st != 200:
                raise SfmcError(f"DE rowset failed (HTTP {st}) for {customer_key}: {raw[:200]!r}")
            payload = json.loads(raw)
            for item in payload.get("items", []):
                row = dict(item.get("keys", {}))
                row.update(item.get("values", {}))
                out.append(row)
            count = payload.get("count", len(out))
            if page * page_size >= count or not payload.get("items"):
                break
            page += 1
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "retrieve or get_send or de_rows" -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): SFMC client (auth, SOAP paging, events, REST rowset)"
```

---

### Task 5: Metric transforms (dedup, US date, bounce split, booklet)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Produces: `dedup_by_subscriber(rows) -> list[dict]`; `usdate(iso) -> str`; `bounce_kind(row) -> str`; `bounce_reason(row) -> str`; `booklet_rows(clicks, selector) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py`:

```python
def test_dedup_keeps_earliest_per_subscriber():
    rows = [
        {"SubscriberKey": "a@x", "EventDate": "2026-06-02T10:00:00"},
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T09:00:00"},
        {"SubscriberKey": "b@x", "EventDate": "2026-06-01T08:00:00"},
    ]
    out = pr.dedup_by_subscriber(rows)
    keys = {r["SubscriberKey"]: r["EventDate"] for r in out}
    assert keys == {"a@x": "2026-06-01T09:00:00", "b@x": "2026-06-01T08:00:00"}


def test_usdate_formats_us_style():
    assert pr.usdate("2026-06-24T14:05:00") == "6/24/2026 2:05 PM"
    assert pr.usdate("2026-01-03T00:30:00") == "1/3/2026 12:30 AM"
    assert pr.usdate("") == ""


def test_bounce_kind_and_reason():
    assert pr.bounce_kind({"BounceCategory": "Hard bounce - Bad address"}) == "hard"
    assert pr.bounce_kind({"BounceCategory": "Soft bounce - Mailbox full"}) == "soft"
    assert pr.bounce_kind({"BounceCategory": "Technical/Other"}) == "block"
    assert pr.bounce_kind({"BounceCategory": None}) == "block"
    assert pr.bounce_reason({"BounceCategory": "Hard bounce - Bad address"}) == "Bad address"
    assert pr.bounce_reason({"BounceCategory": "Block"}) == "Block"


def test_booklet_rows_filters_by_selector_and_dedups():
    clicks = [
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T09:00:00", "URL": "https://c.giftplans.org/index.php?cID=5&v=enlA&utm=1"},
        {"SubscriberKey": "a@x", "EventDate": "2026-06-01T10:00:00", "URL": "https://c.giftplans.org/index.php?cID=5&v=enlA&utm=2"},
        {"SubscriberKey": "c@x", "EventDate": "2026-06-01T09:00:00", "URL": "https://c.giftplans.org/article-1"},
    ]
    out = pr.booklet_rows(clicks, "v=enlA")
    assert [r["SubscriberKey"] for r in out] == ["a@x"]  # deduped, article excluded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "dedup or usdate or bounce or booklet" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement (ported from the working `gen_csvs.py`)**

Add to `scripts/pull_reports.py`:

```python
def dedup_by_subscriber(rows):
    """Keep the earliest EventDate row per SubscriberKey (stable)."""
    seen = set()
    out = []
    for r in sorted(rows, key=lambda x: x.get("EventDate") or ""):
        k = r.get("SubscriberKey")
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def usdate(iso):
    """ISO 'YYYY-MM-DDThh:mm:ss' -> US 'M/D/YYYY h:mm AM/PM' (matches UI export)."""
    if not iso:
        return ""
    d, _, t = iso.partition("T")
    try:
        y, mo, da = d.split("-")
        hh = int(t.split(":")[0])
        mm = t.split(":")[1][:2]
    except Exception:
        return iso
    ap = "AM" if hh < 12 else "PM"
    h12 = hh % 12 or 12
    return f"{int(mo)}/{int(da)}/{int(y)} {h12}:{mm} {ap}"


def bounce_kind(row):
    """Hard/Soft explicit; everything else -> block (== Send.OtherBounces line)."""
    c = (row.get("BounceCategory") or "").lower()
    return "hard" if c.startswith("hard") else "soft" if c.startswith("soft") else "block"


def bounce_reason(row):
    c = row.get("BounceCategory") or ""
    return c.split(" - ", 1)[1] if " - " in c else c


def booklet_rows(clicks, selector):
    """Deduped clicks whose URL contains the per-send booklet selector."""
    if not selector:
        return []
    matched = [r for r in clicks if selector.lower() in (r.get("URL") or "").lower()]
    return dedup_by_subscriber(matched)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "dedup or usdate or bounce or booklet" -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): metric transforms (dedup, US date, bounce, booklet)"
```

---

### Task 6: Data-driven engagement metrics + core gate + CSV writing

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `dedup_by_subscriber`, `usdate`, `bounce_kind`, `bounce_reason`, `booklet_rows`, `tracking.naming`.
- Produces:
  - `compute_metrics(events: dict, booklet_selector: str) -> dict` — `events` is `{"sent","open","click","bounce","unsub"}` of raw row lists; returns `{"rows": {metric: [rows]}, "counts": {metric: int}, "BH": int, "Total Opens": int, "Total Clicks": int}`. Metric keys use `naming.METRIC_DESCRIPTIONS` names plus `"Request Your"`.
  - `evaluate_core_gate(counts: dict, confirm_zero: bool) -> tuple[str, list[str]]` — returns `(status, flags)` where status ∈ `{"ok","failed","needs_confirmation"}`.
  - `optional_flags(counts: dict) -> list[str]` — flags for zero/absent optional metrics.
  - `write_engagement_csvs(folder: Path, identity, metric_rows: dict) -> list[str]` — writes a CSV per metric with ≥1 row; returns filenames. Uses `naming.finished_csv_name`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py` (add `from tracking import naming` near the top after the module load):

```python
from tracking import naming


def _events(sent=2, opens=2, clicks=2, bounces=(), unsubs=0, booklet_url="v=enlA"):
    mk = lambda i, extra=None: {"SubscriberKey": f"u{i}@x", "EventDate": f"2026-06-0{i%9+1}T09:00:00", **(extra or {})}
    return {
        "sent": [mk(i) for i in range(sent)],
        "open": [mk(i) for i in range(opens)],
        "click": [mk(i, {"URL": f"https://c/index.php?cID=5&{booklet_url}"}) for i in range(clicks)],
        "bounce": list(bounces),
        "unsub": [mk(i) for i in range(unsubs)],
    }


def _identity():
    return naming.SendIdentity(client="Example College", season="Spring", year="2026", type="eNL")


def test_compute_metrics_counts_and_bh():
    m = pr.compute_metrics(_events(sent=3, opens=2, clicks=2), "v=enlA")
    assert m["counts"]["Total Sent"] == 3
    assert m["counts"]["Unique Opens"] == 2
    assert m["counts"]["Unique Clicks"] == 2
    assert m["BH"] == 2  # both clicks match v=enlA, distinct subscribers


def test_core_gate_total_sent_zero_fails():
    status, flags = pr.evaluate_core_gate({"Total Sent": 0, "Unique Opens": 5, "Unique Clicks": 5}, False)
    assert status == "failed"


def test_core_gate_zero_opens_needs_confirmation_then_released():
    status, _ = pr.evaluate_core_gate({"Total Sent": 9, "Unique Opens": 0, "Unique Clicks": 3}, False)
    assert status == "needs_confirmation"
    status2, _ = pr.evaluate_core_gate({"Total Sent": 9, "Unique Opens": 0, "Unique Clicks": 3}, True)
    assert status2 == "ok"


def test_optional_flags_for_zero_metrics():
    flags = pr.optional_flags({"Block Bounces": 0, "Unsubscribes": 4, "Request Your": 0})
    joined = " ".join(flags)
    assert "Block Bounces" in joined and "Request Your" in joined
    assert "Unsubscribes" not in joined


def test_write_engagement_csvs_data_driven(tmp_path):
    m = pr.compute_metrics(_events(sent=2, opens=2, clicks=1), "v=enlA")
    # Force one optional metric empty to prove the empty file is skipped.
    m["rows"]["Block Bounces"] = []
    files = pr.write_engagement_csvs(tmp_path, _identity(), m["rows"])
    assert "Example College Spring 2026 eNL - Total Sent.csv" in files
    assert not any("Block Bounces" in f for f in files)  # empty -> skipped
    # Header + email-as-key content check on Total Sent.
    content = (tmp_path / "Example College Spring 2026 eNL - Total Sent.csv").read_text(encoding="utf-8-sig")
    assert content.splitlines()[0] == "Subscriber Key,Email Address"
    assert "u0@x,u0@x" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "compute_metrics or core_gate or optional_flags or engagement_csvs" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `scripts/pull_reports.py` (add `import csv` to imports; add `from tracking import naming`):

```python
import csv
from tracking import naming

# CSV column layout per metric: (header, row-builder). SubscriberKey doubles as
# Email Address in this account (spec §5.2). Booklet reuses the clicks layout.
_CORE = ("Total Sent", "Unique Opens", "Unique Clicks")
_OPTIONAL = ("Hard Bounces", "Soft Bounces", "Block Bounces", "Unsubscribes", "Request Your")

_CSV_LAYOUT = {
    "Total Sent": (["Subscriber Key", "Email Address"],
                   lambda r: [r["SubscriberKey"], r["SubscriberKey"]]),
    "Unique Opens": (["Subscriber Key", "Email Address", "Time Opened"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate"))]),
    "Unique Clicks": (["Subscriber Key", "Email Address", "Click-Through Time", "Link Clicked"],
                      lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")), r.get("URL")]),
    "Request Your": (["Subscriber Key", "Email Address", "Click-Through Time", "Link Clicked"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")), r.get("URL")]),
    "Unsubscribes": (["Subscriber Key", "Email Address", "Unsubscribed Time"],
                     lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate"))]),
}
_BOUNCE_HEADER = ["Subscriber Key", "Email Address", "Undelivered Time", "Bounce Reason", "Bounce Description"]
_BOUNCE_ROW = lambda r: [r["SubscriberKey"], r["SubscriberKey"], usdate(r.get("EventDate")),
                         bounce_reason(r), r.get("SMTPReason")]
for _b in ("Hard Bounces", "Soft Bounces", "Block Bounces"):
    _CSV_LAYOUT[_b] = (_BOUNCE_HEADER, _BOUNCE_ROW)


def compute_metrics(events, booklet_selector):
    sent = dedup_by_subscriber(events["sent"])
    opens = dedup_by_subscriber(events["open"])
    clicks_raw = events["click"]
    clicks = dedup_by_subscriber(clicks_raw)
    booklet = booklet_rows(clicks_raw, booklet_selector)
    unsub = dedup_by_subscriber(events["unsub"])
    bounces = {kind: dedup_by_subscriber([r for r in events["bounce"] if bounce_kind(r) == kind])
               for kind in ("hard", "soft", "block")}
    rows = {
        "Total Sent": sent,
        "Unique Opens": opens,
        "Unique Clicks": clicks,
        "Request Your": booklet,
        "Hard Bounces": bounces["hard"],
        "Soft Bounces": bounces["soft"],
        "Block Bounces": bounces["block"],
        "Unsubscribes": unsub,
    }
    counts = {k: len(v) for k, v in rows.items()}
    return {
        "rows": rows,
        "counts": counts,
        "BH": len(booklet),
        "Total Opens": len(events["open"]),
        "Total Clicks": len(events["click"]),
    }


def evaluate_core_gate(counts, confirm_zero):
    if counts.get("Total Sent", 0) == 0:
        return "failed", ["Total Sent = 0 — pull looks broken; refusing to proceed."]
    zeros = [m for m in ("Unique Opens", "Unique Clicks") if counts.get(m, 0) == 0]
    if zeros and not confirm_zero:
        return "needs_confirmation", [f"{m} = 0 — confirm before writing side effects." for m in zeros]
    return "ok", [f"{m} = 0 — confirmed by operator." for m in zeros] if zeros else []


def optional_flags(counts):
    return [f"{m}: 0 — no file written" for m in _OPTIONAL if counts.get(m, 0) == 0]


def write_engagement_csvs(folder, identity, metric_rows):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for metric, (header, build_row) in _CSV_LAYOUT.items():
        rows = metric_rows.get(metric) or []
        if not rows:
            continue  # data-driven: skip empty/absent metrics
        desc = naming.REQUEST_FILE_DESCRIPTION if metric == "Request Your" else metric
        name = naming.finished_csv_name(identity, desc)
        with open(folder / name, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(build_row(r) for r in rows)
        written.append(name)
    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "compute_metrics or core_gate or optional_flags or engagement_csvs" -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): data-driven engagement metrics, core gate, CSV writing"
```

---

### Task 7: Styled report PDF (values from Send + events, no side-channel)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `tracking.naming`.
- Produces: `render_report_pdf(path: Path, identity, send: dict, total_opens: int, total_clicks: int, bh: int) -> None`. All display values derive from `send` (the `Send` object dict) + the three int params.

- [ ] **Step 1: Write the failing test (smoke: real PDF with the right numbers)**

Append to `tests/test_pull_reports.py` (add `import pdfplumber` at top):

```python
import pdfplumber


def test_render_report_pdf_smoke(tmp_path):
    send = {
        "ID": "691866", "Subject": "Charitable Solutions", "SentDate": "2026-06-24T14:05:00",
        "NumberSent": "1086", "NumberDelivered": "1051", "UniqueOpens": "415", "UniqueClicks": "9",
        "HardBounces": "6", "SoftBounces": "23", "OtherBounces": "6", "Unsubscribes": "16",
    }
    out = tmp_path / "r.pdf"
    pr.render_report_pdf(out, _identity(), send, total_opens=675, total_clicks=25, bh=2)
    assert out.read_bytes().startswith(b"%PDF")
    with pdfplumber.open(out) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
    assert "Engagement Tracking Report" in text
    assert "1,051" in text and "415" in text  # delivered + unique opens present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k render_report_pdf -v`
Expected: FAIL — `render_report_pdf` not defined.

- [ ] **Step 3: Implement (ported verbatim from approved `gen_report.PRESERVED.py`; the `plans[...]` side-channel is replaced by `send` fields + params)**

Add the reportlab imports to the top of `scripts/pull_reports.py`:

```python
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_NAVY = colors.HexColor("#1f3a5f"); _NAVY2 = colors.HexColor("#26425f"); _GREEN = colors.HexColor("#2f7d68")
_ORANGE = colors.HexColor("#c2792f"); _GRAY = colors.HexColor("#64707d"); _LINE = colors.HexColor("#d9dee5")
_ALT = colors.HexColor("#f3f5f7"); _TXT = colors.HexColor("#33404d"); _MUTE = colors.HexColor("#8892a0")


def _sty(**k):
    k.setdefault("leading", k.get("fontSize", 10) * 1.25)
    return ParagraphStyle("x", **k)


def _san(t):
    t = t or ""
    for a, b in [("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("…", "..."), (" ", " ")]:
        t = t.replace(a, b)
    return t.strip()


def _kpi(num, label):
    p = ParagraphStyle("k", textColor=colors.white, alignment=1, leading=20)
    return Paragraph(f'<font size="17"><b>{num}</b></font><br/><font size="7.5">{label}</font>', p)


def _perf_table(rows, indent_labels=()):
    t = Table([[r[0], r[1]] for r in rows], colWidths=[1.9 * inch, 1.05 * inch])
    ts = [('FONTSIZE', (0, 0), (-1, -1), 9.5), ('TEXTCOLOR', (0, 0), (0, -1), _GRAY),
          ('TEXTCOLOR', (1, 0), (1, -1), _TXT), ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
          ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('LINEBELOW', (0, 0), (-1, -2), 0.5, _LINE),
          ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]
    for i in indent_labels:
        ts.append(('LEFTPADDING', (0, i), (0, i), 16))
    t.setStyle(TableStyle(ts))
    return t


def render_report_pdf(path, identity, send, total_opens, total_clicks, bh):
    def n(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0

    sent = n(send.get("NumberSent")); deliv = n(send.get("NumberDelivered"))
    uo = n(send.get("UniqueOpens")); uc = n(send.get("UniqueClicks")); topen = n(total_opens)
    hard = n(send.get("HardBounces")); soft = n(send.get("SoftBounces")); block = n(send.get("OtherBounces"))
    unsub = n(send.get("Unsubscribes")); tb = hard + soft + block
    dr = deliv / sent * 100 if sent else 0
    orr = uo / deliv * 100 if deliv else 0
    ctr = uc / deliv * 100 if deliv else 0
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.45 * inch)
    E = []
    E.append(Paragraph("Engagement Tracking Report",
                       _sty(fontName="Helvetica-Bold", fontSize=22, textColor=_NAVY, spaceAfter=2)))
    E.append(Paragraph(_san(f"{identity.client}  -  {identity.season} {identity.year} {identity.type}"),
                       _sty(fontName="Helvetica", fontSize=10.5, textColor=_MUTE, spaceAfter=12)))
    meta = Table([["Job ID", send.get("ID")], ["Subject", _san(send.get("Subject"))],
                  ["Date Sent", (send.get("SentDate") or "").replace("T", " ")],
                  ["Total Sent", f"{sent:,}"], ["Total Not Sent", "0"]], colWidths=[1.4 * inch, 4.6 * inch])
    meta.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 9.5), ('TEXTCOLOR', (0, 0), (0, -1), _GRAY),
        ('TEXTCOLOR', (1, 0), (1, -1), _TXT), ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, _LINE), ('TOPPADDING', (0, 0), (-1, -1), 3.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5)]))
    E.append(meta); E.append(Spacer(1, 9))
    tiles = Table([[_kpi(f"{deliv:,}", f"Delivered - {dr:.3f}%"), _kpi(f"{uo:,}", f"Unique Opens - {orr:.3f}%"),
                    _kpi(f"{uc:,}", f"Unique Clicks - {ctr:.3f}%"), _kpi(f"{unsub:,}", "Unsubscribes")]],
                  colWidths=[1.62 * inch] * 4, rowHeights=[0.54 * inch])
    tiles.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, 0), _NAVY2), ('BACKGROUND', (1, 0), (1, 0), _GREEN),
        ('BACKGROUND', (2, 0), (2, 0), _ORANGE), ('BACKGROUND', (3, 0), (3, 0), _GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))
    E.append(tiles); E.append(Spacer(1, 11))
    hdr = _sty(fontName="Helvetica-Bold", fontSize=12, textColor=_NAVY, spaceAfter=5)
    left = [Paragraph("Send Performance", hdr), _perf_table(
        [("Delivery Rate", f"{dr:.3f}%"), ("Total Bounces", f"{tb:,}"), ("Hard Bounce", f"{hard:,}"),
         ("Soft Bounce", f"{soft:,}"), ("Block Bounce", f"{block:,}"), ("Delivered", f"{deliv:,}")],
        indent_labels=(2, 3, 4))]
    right = [Paragraph("Open Performance", hdr), _perf_table(
        [("Open Rate", f"{orr:.3f}%"), ("Total Opens", f"{topen:,}"), ("Unique Opens", f"{uo:,}"),
         ("Total Clicks", f"{total_clicks:,}"), ("Unique Clicks", f"{uc:,}"), ("Booklet clicks (BH)", f"{bh:,}")])]
    two = Table([[left, right]], colWidths=[3.15 * inch, 3.15 * inch])
    two.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (0, 0), 0),
                             ('LEFTPADDING', (1, 0), (1, 0), 18)]))
    E.append(two); E.append(Spacer(1, 11))
    E.append(Paragraph("Inbox Activity", hdr))
    ia = [["Metric", "Total", "Unique"], ["Opens", f"{topen:,}", f"{uo:,}"], ["Clicks", f"{total_clicks:,}", f"{uc:,}"],
          ["Forwards", "0", "0"], ["Conversions", "0", "0"], ["Unsubscribes", "-", f"{unsub:,}"]]
    iat = Table(ia, colWidths=[3.0 * inch, 1.65 * inch, 1.65 * inch])
    iast = [('BACKGROUND', (0, 0), (-1, 0), _NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9.5),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'), ('TEXTCOLOR', (0, 1), (-1, -1), _TXT),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, _LINE)]
    for r in range(2, 6, 2):
        iast.append(('BACKGROUND', (0, r), (-1, r), _ALT))
    iat.setStyle(TableStyle(iast)); E.append(iat); E.append(Spacer(1, 9))
    E.append(Paragraph(f"Unengaged Subscribers (of {deliv:,} delivered)", hdr))
    E.append(_perf_table([("Did not open", f"{deliv - uo:,}"), ("Did not click", f"{deliv - uc:,}")]))
    E.append(Spacer(1, 9))
    d = Drawing(430, 84); bc = HorizontalBarChart()
    bc.x = 70; bc.y = 4; bc.height = 72; bc.width = 300
    bc.data = [[sent, deliv, uo, uc]]; bc.categoryAxis.categoryNames = ["Sent", "Delivered", "Opened", "Clicked"]
    bc.categoryAxis.labels.fontSize = 8; bc.valueAxis.visible = False
    bc.valueAxis.valueMin = 0; bc.valueAxis.valueMax = sent * 1.15 if sent else 1
    bc.bars[0].fillColor = _NAVY; bc.bars.strokeColor = None; bc.barWidth = 12; bc.groupSpacing = 7
    bc.barLabelFormat = '%d'; bc.barLabels.fontSize = 8; bc.barLabels.dx = 4
    bc.barLabels.boxAnchor = 'w'; bc.barLabels.fillColor = _TXT
    d.add(bc)
    foot = Paragraph("Generated by engagement-tracker from Marketing Cloud API data (Send object + open/click "
                     "tracking). Bounce sub-split may differ by a few from the UI; the total is exact. Not "
                     "ExactTarget's document.", _sty(fontName="Helvetica-Oblique", fontSize=7.5, textColor=_MUTE))
    E.append(KeepTogether([Paragraph("Delivery Funnel", hdr), d, Spacer(1, 8), foot]))
    doc.build(E)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k render_report_pdf -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): styled report PDF from Send object + events"
```

---

### Task 8: API → SheetPlan mapping (reuse write_send, not build_sheet_plan)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `tracking.sheet.SheetPlan`, `tracking.sheet.write_send`.
- Produces: `build_api_sheet_plan(send: dict, total_opens: int, bh: int) -> sheet.SheetPlan`. Populates the exact `sheet.COLUMN_SOURCES` headers from API aggregates; omits `"Booklet landing page unique clicks"` when `bh == 0`; rates are decimals rounded to 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py`:

```python
def test_build_api_sheet_plan_values():
    send = {"NumberSent": "1086", "NumberDelivered": "1051", "UniqueOpens": "415",
            "UniqueClicks": "9", "Subject": "Hello"}
    plan = pr.build_api_sheet_plan(send, total_opens=675, bh=2)
    v = plan.values
    assert v["# Total sent"] == 1086
    assert v["# Delivered"] == 1051
    assert v["# Unique opens"] == 415
    assert v["# Unique clicks"] == 9
    assert v["# Total opens"] == 675
    assert v["Booklet landing page unique clicks"] == 2
    assert v["Subject line"] == "Hello"
    assert round(v["Unique open rate %"], 5) == round(415 / 1051, 5)


def test_build_api_sheet_plan_omits_zero_booklet():
    send = {"NumberSent": "100", "NumberDelivered": "100", "UniqueOpens": "10",
            "UniqueClicks": "0", "Subject": "Hi"}
    plan = pr.build_api_sheet_plan(send, total_opens=12, bh=0)
    assert "Booklet landing page unique clicks" not in plan.values
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k api_sheet_plan -v`
Expected: FAIL — `build_api_sheet_plan` not defined.

- [ ] **Step 3: Implement (construct SheetPlan directly — do NOT call build_sheet_plan)**

Add to `scripts/pull_reports.py` (add `from tracking import sheet`):

```python
from tracking import sheet


def build_api_sheet_plan(send, total_opens, bh):
    """Map SFMC aggregates directly onto the Print Status Report headers.

    Deliberately does NOT reuse sheet.build_sheet_plan (that is PDF/file-count
    oriented, spec §5.5). Only sheet.write_send's row-match/fill-blank logic is
    reused, on the SheetPlan built here.
    """
    def n(x):
        try:
            return int(x or 0)
        except (TypeError, ValueError):
            return 0

    sent = n(send.get("NumberSent")); deliv = n(send.get("NumberDelivered"))
    uo = n(send.get("UniqueOpens")); uc = n(send.get("UniqueClicks"))
    plan = sheet.SheetPlan()
    plan.values["# Total sent"] = sent
    plan.values["# Delivered"] = deliv
    plan.values["# Unique opens"] = uo
    plan.values["# Unique clicks"] = uc
    plan.values["# Total opens"] = int(total_opens)
    if bh:
        plan.values["Booklet landing page unique clicks"] = int(bh)
    else:
        plan.flags.append("BH = 0 — booklet cell omitted; confirm the booklet link.")
    if deliv:
        plan.values["Unique open rate %"] = round(uo / deliv, 5)
        plan.values["Unique click-through %"] = round(uc / deliv, 5)
    subject = send.get("Subject")
    if subject:
        plan.values["Subject line"] = subject
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k api_sheet_plan -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): API->SheetPlan mapping (reuse write_send only)"
```

---

### Task 9: Calendar tab derivation, block find, fill-blank mark

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Produces:
  - `candidate_tabs(send_date_iso: str) -> list[str]` — `["<Month YYYY>", "<next Month YYYY>"]`.
  - `col_to_a1(col: int) -> str` (0-indexed → A1 letters).
  - `find_calendar_blocks(grid: list[list], client: str, type_: str) -> list[tuple[int,int]]` — every `(row, name_col)` whose name matches client and whose `+1` cell matches type (name_cols 0,5,10,15,20).
  - `mark_calendar(writer_for_tab, send_date_iso, identity, mark, fill_blanks_only=True) -> dict` — iterates candidate tabs, writes the `+4` cell if blank; returns `{"tab","cell","status","candidates"}`. `writer_for_tab(tab)` returns an object with `get_values()` and `update_cell(row,col,value)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py`:

```python
def test_candidate_tabs_covers_send_month_and_next():
    assert pr.candidate_tabs("2026-06-24T14:05:00") == ["June 2026", "July 2026"]
    assert pr.candidate_tabs("2026-12-30T00:00:00") == ["December 2026", "January 2027"]


def test_col_to_a1():
    assert pr.col_to_a1(0) == "A"
    assert pr.col_to_a1(24) == "Y"
    assert pr.col_to_a1(26) == "AA"


def test_find_calendar_blocks_matches_client_and_type():
    grid = [
        ["", "", "", "", "", "", "", "", "", ""],
        ["Mount Vernon School", "eQC", "", "", "", "Other Client", "eNL", "", "", ""],
    ]
    blocks = pr.find_calendar_blocks(grid, "Mount Vernon School", "eQC")
    assert blocks == [(1, 0)]


class FakeCalWriter:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    def get_values(self):
        return self.grid

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


def test_mark_calendar_fills_blank_plus4_cell():
    grid = [["", "", "", "", "", "", "", "", "", ""],
            ["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]]
    writers = {"June 2026": FakeCalWriter(grid), "July 2026": FakeCalWriter([[]])}
    ident = naming.SendIdentity(client="Mount Vernon School", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["tab"] == "June 2026" and res["cell"] == "E2" and res["status"] == "written"
    assert writers["June 2026"].updates == [(1, 4, "7/15 JS")]


def test_mark_calendar_not_found_lists_candidates():
    writers = {"June 2026": FakeCalWriter([["Nobody", "eNL"]]), "July 2026": FakeCalWriter([[]])}
    ident = naming.SendIdentity(client="Ghost", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["status"] == "not-found"
    assert res["cell"] is None
    assert res["searched_tabs"] == ["June 2026", "July 2026"]


def test_mark_calendar_ambiguous_across_both_tabs_writes_nothing():
    june = FakeCalWriter([["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]])
    july = FakeCalWriter([["Mount Vernon School", "eQC", "", "", "", "", "", "", "", ""]])
    writers = {"June 2026": june, "July 2026": july}
    ident = naming.SendIdentity(client="Mount Vernon School", season="Summer", year="2026", type="eQC")
    res = pr.mark_calendar(lambda tab: writers[tab], "2026-06-24T00:00:00", ident, "7/15 JS")
    assert res["status"] == "ambiguous"
    assert len(res["candidates"]) == 2  # both tabs scanned before deciding
    assert {c["tab"] for c in res["candidates"]} == {"June 2026", "July 2026"}
    assert res["candidates"][0]["type"] == "eQC"  # candidates carry client/type
    assert june.updates == [] and july.updates == []  # nothing written when ambiguous
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "candidate_tabs or col_to_a1 or calendar_blocks or mark_calendar" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `scripts/pull_reports.py` (add `import re`):

```python
import re

_CAL_NAME_COLS = (0, 5, 10, 15, 20)


def _cal_key(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def candidate_tabs(send_date_iso):
    """Report task lags the send by ~2 weeks, so search the send month + next."""
    d = datetime.date.fromisoformat((send_date_iso or "").split("T")[0])
    nxt = (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    return [d.strftime("%B %Y"), nxt.strftime("%B %Y")]


def col_to_a1(col):
    s = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        s = chr(65 + rem) + s
    return s


def _cell(grid, r, c):
    row = grid[r] if r < len(grid) else []
    return row[c] if c < len(row) else ""


def find_calendar_blocks(grid, client, type_):
    ck, tk = _cal_key(client), _cal_key(type_)
    out = []
    for r in range(len(grid)):
        for nc in _CAL_NAME_COLS:
            name = _cal_key(_cell(grid, r, nc))
            if name and ck in name and tk and tk == _cal_key(_cell(grid, r, nc + 1)):
                out.append((r, nc))
    return out


def mark_calendar(writer_for_tab, send_date_iso, identity, mark, fill_blanks_only=True):
    """Scan ALL candidate tabs fully, collect every client+type block, THEN decide.
    Never writes until we know there is exactly one match across both months.
    Candidates carry tab/row/client/type so status/dry-run reporting is useful."""
    tabs = candidate_tabs(send_date_iso)
    matches = []      # (tab, writer, grid, row, name_col)
    candidates = []   # operator-facing block descriptors
    for tab in tabs:
        writer = writer_for_tab(tab)
        grid = writer.get_values()
        for (r, nc) in find_calendar_blocks(grid, identity.client, identity.type):
            matches.append((tab, writer, grid, r, nc))
            candidates.append({
                "tab": tab, "row": r + 1, "name_col": col_to_a1(nc),
                "client": _cell(grid, r, nc), "type": _cell(grid, r, nc + 1),
            })
    result = {"tab": None, "cell": None, "status": None,
              "candidates": candidates, "searched_tabs": tabs}
    if len(matches) == 1:
        tab, writer, grid, r, nc = matches[0]
        mark_col = nc + 4
        result.update(tab=tab, cell=f"{col_to_a1(mark_col)}{r + 1}")
        if str(_cell(grid, r, mark_col)).strip() and fill_blanks_only:
            result["status"] = "already"
        else:
            writer.update_cell(r, mark_col, mark)
            result["status"] = "written"
    elif len(matches) > 1:
        result["status"] = "ambiguous"
    else:
        result["status"] = "not-found"
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "candidate_tabs or col_to_a1 or calendar_blocks or mark_calendar" -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): calendar tab derivation + fill-blank mark"
```

---

### Task 10: Lead Scoring — filename, CSV write, Kathryn draft (HIPAA-aware)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `SfmcClient` (`retrieve` for DE lookup + fields; `get_de_rows_rest`), `tracking.drafts.DraftEmail`.
- Produces:
  - `lead_scoring_filename(de_name: str, when: datetime.date) -> str` → `<de_name><YYYYMMDD>.csv`.
  - `resolve_lead_de(client: SfmcClient, de_name_or_key: str, client_name: str) -> tuple[str,str]` → `(name, customer_key)`; fails loud if not found/ambiguous.
  - `de_ordered_columns(client: SfmcClient, customer_key: str) -> list[str]` (by `DataExtensionField.Ordinal`).
  - `write_lead_scoring_csv(folder: Path, de_name: str, columns: list[str], rows: list[dict], when: datetime.date) -> str`.
  - `build_kathryn_draft(identity, lead_file_path: Path, hipaa: bool) -> tracking.drafts.DraftEmail | None` — `None` when `hipaa` (caller flags).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pull_reports.py`:

```python
import datetime as _dt
from tracking.drafts import DraftEmail


def test_lead_scoring_filename():
    name = pr.lead_scoring_filename("sd_Example College - Lead Scoring", _dt.date(2026, 7, 15))
    assert name == "sd_Example College - Lead Scoring20260715.csv"


def test_write_lead_scoring_csv_uses_ordinal_columns(tmp_path):
    cols = ["SubscriberKey", "Score", "Class Year"]
    rows = [{"subscriberkey": "a@x", "score": "5", "class year": "2027"}]
    name = pr.write_lead_scoring_csv(tmp_path, "sd_Example College - Lead Scoring", cols, rows, _dt.date(2026, 7, 15))
    text = (tmp_path / name).read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "SubscriberKey,Score,Class Year"
    assert "a@x,5,2027" in text


def test_build_kathryn_draft_notification_only():
    d = pr.build_kathryn_draft(_identity(), Path("/abs/Lead Scoring/sd_Example College - Lead Scoring20260715.csv"), hipaa=False)
    assert isinstance(d, DraftEmail)
    assert d.to == ["kathryn.baugh@pentera.com"]
    assert d.attachments == []  # notification only, never attach the lead file
    assert "sd_Example College - Lead Scoring20260715.csv" in d.body


def test_build_kathryn_draft_hipaa_skips():
    assert pr.build_kathryn_draft(_identity(), Path("/abs/x.csv"), hipaa=True) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "lead_scoring or kathryn" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement**

Add to `scripts/pull_reports.py` (add `from tracking.drafts import DraftEmail`):

```python
from tracking.drafts import DraftEmail

_KATHRYN = "kathryn.baugh@pentera.com"


def lead_scoring_filename(de_name, when):
    return f"{de_name}{when.strftime('%Y%m%d')}.csv"


def resolve_lead_de(client, de_name_or_key, client_name):
    """Return (Name, CustomerKey). Prefer the explicit manifest value; else derive
    'sd_<Client> - Lead Scoring'. Fail loud if not found or ambiguous."""
    target = (de_name_or_key or "").strip() or f"sd_{client_name} - Lead Scoring"

    def _lookup(prop, value):
        filt = (f'<Filter xsi:type="SimpleFilterPart"><Property>{prop}</Property>'
                f"<SimpleOperator>equals</SimpleOperator><Value>{value}</Value></Filter>")
        return client.retrieve("DataExtension", ["Name", "CustomerKey"], filt)

    rows = _lookup("CustomerKey", target) or _lookup("Name", target)
    if not rows:
        raise SfmcError(f"Lead Scoring DE not found for {target!r}.")
    if len(rows) > 1:
        names = ", ".join(sorted(r.get("Name") or "" for r in rows))
        raise SfmcError(f"Ambiguous Lead Scoring DE for {target!r}: {names}. Set lead_scoring_de explicitly.")
    return rows[0]["Name"], rows[0]["CustomerKey"]


def de_ordered_columns(client, customer_key):
    filt = (f'<Filter xsi:type="SimpleFilterPart"><Property>DataExtension.CustomerKey</Property>'
            f"<SimpleOperator>equals</SimpleOperator><Value>{customer_key}</Value></Filter>")
    rows = client.retrieve("DataExtensionField", ["Name", "Ordinal"], filt)
    def _ord(r):
        o = r.get("Ordinal")
        return int(o) if (o and str(o).isdigit()) else 999
    return [r["Name"] for r in sorted(rows, key=_ord) if r.get("Name")]


def write_lead_scoring_csv(folder, de_name, columns, rows, when):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = lead_scoring_filename(de_name, when)
    # REST rowset lowercases field names; map back to the ordinal-cased column.
    lower = {c.lower(): c for c in columns}
    with open(folder / name, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for r in rows:
            norm = {k.lower(): v for k, v in r.items()}
            w.writerow([norm.get(c.lower(), "") for c in columns])
    return name


def build_kathryn_draft(identity, lead_file_path, hipaa):
    if hipaa:
        return None
    p = Path(lead_file_path)
    body = (
        f"The lead score for {identity.client} {identity.season} {identity.year} {identity.type} "
        f"is ready for Client Access upload.\n\n"
        f"File: {p.name}\nLocation: {p.parent}\n"
    )
    return DraftEmail(to=[_KATHRYN], subject=f"Lead Score Ready - {identity.prefix}", body=body, attachments=[])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "lead_scoring or kathryn" -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): Lead Scoring export + Kathryn notification draft"
```

---

### Task 11: Report delivery draft (blank To, absolute-path attachments)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: `tracking.naming.email_subject`, `tracking.naming.finished_pdf_name`, `tracking.drafts.DraftEmail`.
- Produces: `build_report_draft(identity, folder: Path) -> tracking.drafts.DraftEmail` — `to=[]`, empty body, subject `naming.email_subject`, attachments = the folder's PDF + all `"<prefix> - *.csv"` metric CSVs (excluding `sd_*`), as **absolute** paths, PDF first.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pull_reports.py`:

```python
def test_build_report_draft_blank_to_absolute_attachments(tmp_path):
    ident = _identity()
    folder = tmp_path / ident.folder_name
    folder.mkdir()
    (folder / naming.finished_pdf_name(ident)).write_bytes(b"%PDF-1.4")
    (folder / naming.finished_csv_name(ident, "Total Sent")).write_text("x")
    (folder / "sd_Example College - Lead Scoring20260715.csv").write_text("y")  # must NOT attach
    d = pr.build_report_draft(ident, folder)
    assert d.to == []
    assert d.body == ""
    assert d.subject == naming.email_subject(ident)
    names = [p.name for p in d.attachments]
    assert names[0].endswith("Engagement Tracking Report.pdf")  # PDF first
    assert any(n.endswith("- Total Sent.csv") for n in names)
    assert not any(n.startswith("sd_") for n in names)
    assert all(Path(p).is_absolute() for p in d.attachments)  # MAX_PATH-safe
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k report_draft -v`
Expected: FAIL — `build_report_draft` not defined.

- [ ] **Step 3: Implement**

Add to `scripts/pull_reports.py`:

```python
def build_report_draft(identity, folder):
    folder = Path(folder).resolve()  # absolute -> dodges Windows MAX_PATH on long names
    pdf = folder / naming.finished_pdf_name(identity)
    csvs = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".csv"
        and p.name.startswith(f"{identity.prefix} - ")
        and not p.name.lower().startswith("sd_")
    )
    attachments = ([pdf] if pdf.is_file() else []) + csvs
    return DraftEmail(to=[], subject=naming.email_subject(identity), body="", attachments=attachments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k report_draft -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): report delivery draft (blank To, absolute attachments)"
```

---

### Task 12: Orchestration + CLI (init / build / status, gates, dry-run)

**Files:**
- Modify: `scripts/pull_reports.py`
- Modify: `tests/test_pull_reports.py`

**Interfaces:**
- Consumes: everything above; `tracking.sheet.write_send`, `tracking.sheets_writer.GoogleSheetsWriter`, `tracking.gmail_source.GmailSource`.
- Produces:
  - `require_hipaa(send: dict) -> bool` — returns the bool; raises `SfmcError` if the `hipaa` key is absent.
  - `run_send(send: dict, deps: dict, *, force=False, dry_run=False, skip_drafts=False, skip_calendar=False, confirm_zero_ids: set[str]) -> dict` — runs the pipeline for one send against injected `deps` (sfmc, reports_dir, lead_dir, sheet_writer, calendar_writer_for_tab, draft_writer), returns the enriched send dict. Never raises; captures per-send errors.
  - `cmd_init/cmd_build/cmd_status(args) -> int`, `main(argv=None) -> int`.
- `deps` keys: `{"sfmc": SfmcClient, "reports_dir": Path, "lead_dir": Path, "sheet_writer", "calendar_writer_for_tab": callable, "draft_writer"}`. In tests these are fakes; in `main` they are the real adapters.

- [ ] **Step 1: Write the failing tests (fakes for all IO)**

Append to `tests/test_pull_reports.py`:

```python
class FakeSheetWriter:
    def __init__(self, grid):
        self._grid = grid
        self.updates = []

    def get_values(self):
        return self._grid

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


class FakeDraftWriter:
    def __init__(self):
        self.created = []

    def create_draft(self, draft):
        self.created.append(draft)
        return f"draft-{len(self.created)}"


class FakeSfmc:
    """Returns fixed events/send/DE data without network."""
    def __init__(self, send, events, de_rows, de_cols, de_name="sd_Example College - Lead Scoring", de_key="KEY"):
        self._send = send; self._events = events
        self._de_rows = de_rows; self._de_cols = de_cols
        self._de_name = de_name; self._de_key = de_key

    def authenticate(self): pass
    def get_send(self, sid): return self._send
    def get_events(self, obj, sid, props):
        return self._events[{"SentEvent": "sent", "OpenEvent": "open", "ClickEvent": "click",
                             "BounceEvent": "bounce", "UnsubEvent": "unsub"}[obj]]
    def get_de_rows_rest(self, key): return self._de_rows


def _full_sheet_grid():
    """A Print Status header row with every column build_api_sheet_plan emits,
    plus one all-blank data row for 'Example College' eNL."""
    headers = ["Client", "Type", "# Total sent", "# Delivered", "# Unique opens",
               "# Unique clicks", "# Total opens", "Booklet landing page unique clicks",
               "Unique open rate %", "Unique click-through %", "Subject line"]
    return [[], [], headers, ["Example College", "eNL"] + [""] * (len(headers) - 2)]


def _deps(tmp_path, sfmc, sheet_grid=None, cal_writers=None, draft_writer=None):
    grid = sheet_grid if sheet_grid is not None else [[], [], ["Client", "Type"], []]
    return {
        "sfmc": sfmc,
        "reports_dir": tmp_path / "Completed Reports",
        "lead_dir": tmp_path / "Lead Scoring",
        "sheet_writer": FakeSheetWriter(grid),
        "calendar_writer_for_tab": (lambda tab: (cal_writers or {}).get(tab, FakeCalWriter([[]]))),
        "draft_writer": draft_writer or FakeDraftWriter(),
        # For lead scoring, patch the DE resolution to avoid SOAP in unit tests:
        "lead_de_override": ("sd_Example College - Lead Scoring", "KEY", ["SubscriberKey", "Score"]),
    }


def _send_input(**over):
    base = {"client": "Example College", "season": "Spring", "year": "2026", "type": "eNL",
            "send_id": "1", "booklet_selector": "v=enlA", "lead_scoring_de": "", "hipaa": False,
            "confirm_zero": False}
    base.update(over)
    return base


def test_require_hipaa_absent_raises():
    import pytest
    with pytest.raises(pr.SfmcError):
        pr.require_hipaa({"client": "X"})  # no hipaa key
    assert pr.require_hipaa({"hipaa": True}) is True


def test_run_send_happy_path_writes_everything(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "3",
            "NumberDelivered": "3", "UniqueOpens": "2", "UniqueClicks": "2",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=3, opens=2, clicks=2)
    sfmc = FakeSfmc(send, ev, de_rows=[{"subscriberkey": "a@x", "score": "5"}], de_cols=["SubscriberKey", "Score"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, sheet_grid=_full_sheet_grid(), draft_writer=dw)
    out = pr.run_send(_send_input(), deps, confirm_zero_ids=set())
    assert out["status"] == "complete"
    assert deps["sheet_writer"].updates  # real sheet write happened (non-dry mode)
    assert out["metrics"]["Total Sent"] == 3
    assert (tmp_path / "Completed Reports" / "Example College - Spring 2026 eNL"
            / "Example College Spring 2026 eNL - Engagement Tracking Report.pdf").exists()
    assert out["lead_scoring_file"].startswith("sd_Example College - Lead Scoring")
    assert len(dw.created) == 2  # report + kathryn


def test_run_send_needs_confirmation_skips_side_effects(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "5",
            "NumberDelivered": "5", "UniqueOpens": "0", "UniqueClicks": "0",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=5, opens=0, clicks=0)
    sfmc = FakeSfmc(send, ev, de_rows=[], de_cols=["SubscriberKey"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, draft_writer=dw)
    out = pr.run_send(_send_input(), deps, confirm_zero_ids=set())
    assert out["status"] == "needs_confirmation"
    assert out["sheet"]["status"] == "skipped"
    assert len(dw.created) == 0  # drafts blocked


def test_run_send_dry_run_computes_plan_but_writes_nothing(tmp_path):
    send = {"ID": "1", "Subject": "Hi", "SentDate": "2026-06-24T00:00:00", "NumberSent": "3",
            "NumberDelivered": "3", "UniqueOpens": "2", "UniqueClicks": "2",
            "HardBounces": "0", "SoftBounces": "0", "OtherBounces": "0", "Unsubscribes": "0"}
    ev = _events(sent=3, opens=2, clicks=2)
    sfmc = FakeSfmc(send, ev, de_rows=[{"subscriberkey": "a@x"}], de_cols=["SubscriberKey"])
    dw = FakeDraftWriter()
    deps = _deps(tmp_path, sfmc, sheet_grid=_full_sheet_grid(), draft_writer=dw)
    out = pr.run_send(_send_input(), deps, dry_run=True, confirm_zero_ids=set())
    # Nothing is mutated:
    assert not (tmp_path / "Completed Reports").exists()   # no CSV/PDF files
    assert not (tmp_path / "Lead Scoring").exists()        # no lead file
    assert dw.created == []                                # no drafts created
    assert deps["sheet_writer"].updates == []             # no real sheet write
    # ...but the full plan IS computed (spec §4):
    assert out["status"] == "dry-run"
    assert out["sheet"]["cells"]["# Total sent"]          # a real A1 ref for the matched row
    assert out["drafts"]["report"]["to"] == []
    assert out["drafts"]["report"]["attachments"]         # pdf + csvs listed
    assert out["calendar"]["status"].startswith("dry-run")
    assert out["lead_scoring_file"].startswith("sd_Example College - Lead Scoring")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "require_hipaa or run_send" -v`
Expected: FAIL — `require_hipaa` / `run_send` not defined.

- [ ] **Step 3: Implement orchestration + CLI**

Add to `scripts/pull_reports.py` (add `import argparse`, `import os`; `from tracking import sheets_writer`):

```python
import argparse
import os

from tracking import sheets_writer

_EVENT_PROPS = {
    "SentEvent": ["SubscriberKey", "EventDate"],
    "OpenEvent": ["SubscriberKey", "EventDate"],
    "ClickEvent": ["SubscriberKey", "EventDate", "URL"],
    "BounceEvent": ["SubscriberKey", "EventDate", "BounceCategory", "SMTPReason"],
    "UnsubEvent": ["SubscriberKey", "EventDate"],
}


class _RecordingWriter:
    """Wraps a SheetWriter for --dry-run: reads real values, records intended
    writes instead of performing them (spec §4: dry-run mutates nothing)."""

    def __init__(self, source):
        self._source = source
        self.updates = []

    def get_values(self):
        return self._source.get_values()

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))


def require_hipaa(send):
    if "hipaa" not in send:
        raise SfmcError("send is missing the required 'hipaa' flag (no default allowed).")
    return bool(send["hipaa"])


def _identity_of(send):
    return naming.SendIdentity(client=send["client"], season=send["season"],
                              year=send["year"], type=send["type"])


def run_send(send, deps, *, force=False, dry_run=False, skip_drafts=False,
             skip_calendar=False, confirm_zero_ids=frozenset()):
    out = dict(send)
    out.setdefault("flags", [])
    out["errors"] = []
    try:
        hipaa = require_hipaa(send)
        identity = _identity_of(send)
        sfmc = deps["sfmc"]
        sfmc.authenticate()
        send_obj = sfmc.get_send(send["send_id"])
        events = {key: sfmc.get_events(obj, send["send_id"], _EVENT_PROPS[obj])
                  for obj, key in [("SentEvent", "sent"), ("OpenEvent", "open"),
                                   ("ClickEvent", "click"), ("BounceEvent", "bounce"),
                                   ("UnsubEvent", "unsub")]}
        m = compute_metrics(events, send.get("booklet_selector", ""))
        counts = dict(m["counts"]); counts["Delivered"] = int(send_obj.get("NumberDelivered") or 0)
        confirm = bool(send.get("confirm_zero")) or (send["send_id"] in confirm_zero_ids)
        status, gate_flags = evaluate_core_gate(m["counts"], confirm)
        out["flags"] += gate_flags + optional_flags(m["counts"])
        out["send_date"] = send_obj.get("SentDate")
        out["metrics"] = {**m["counts"], "BH": m["BH"], "Total Opens": m["Total Opens"],
                          "Total Clicks": m["Total Clicks"], "Delivered": counts["Delivered"]}

        folder = deps["reports_dir"] / identity.folder_name
        if status == "failed":
            out["status"] = "failed"; out["errors"].append(gate_flags[0]); return out

        if dry_run:
            # Full non-mutating plan (spec §4): compute CSV set, sheet row/cells,
            # calendar tab/cell/candidates, and draft recipients/attachments —
            # writing no files, no drafts, no sheet/calendar cells.
            csv_names = [naming.finished_csv_name(
                            identity, naming.REQUEST_FILE_DESCRIPTION if k == "Request Your" else k)
                         for k, v in m["rows"].items() if v]
            pdf_name = naming.finished_pdf_name(identity)
            out["csv_files"] = csv_names
            out["pdf_file"] = pdf_name
            override = deps.get("lead_de_override")
            de_name = override[0] if override else resolve_lead_de(
                sfmc, send.get("lead_scoring_de", ""), identity.client)[0]
            out["lead_scoring_file"] = lead_scoring_filename(de_name, datetime.date.today())
            plan = build_api_sheet_plan(send_obj, m["Total Opens"], m["BH"])
            out["flags"] += plan.flags
            rec = _RecordingWriter(deps["sheet_writer"])
            try:
                written = sheet.write_send(rec, identity, plan, fill_blanks_only=not force)
                out["sheet"] = {"status": "dry-run",
                                "cells": {h: f"{col_to_a1(c)}{r + 1}" for h, (r, c) in written.items()}}
            except sheet.SheetError as exc:
                out["sheet"] = {"status": "dry-run", "error": str(exc)}
                out["flags"].append(f"sheet (dry-run): {exc}")
            mark = f"{datetime.date.today().month}/{datetime.date.today().day} {deps.get('mark_initials', 'JS')}"
            out["calendar"] = mark_calendar(
                lambda tab: _RecordingWriter(deps["calendar_writer_for_tab"](tab)),
                send_obj.get("SentDate"), identity, mark, fill_blanks_only=not force)
            out["calendar"]["status"] = f"dry-run:{out['calendar']['status']}"
            out["drafts"] = {
                "status": "dry-run",
                "report": {"to": [], "subject": naming.email_subject(identity),
                           "attachments": [pdf_name] + csv_names},
                "kathryn": (None if hipaa else
                            {"to": [_KATHRYN], "subject": f"Lead Score Ready - {identity.prefix}"}),
            }
            out["status"] = "dry-run"
            return out

        # Always-safe local artifacts (files only).
        out["csv_files"] = write_engagement_csvs(folder, identity, m["rows"])
        pdf_name = naming.finished_pdf_name(identity)
        render_report_pdf(folder / pdf_name, identity, send_obj, m["Total Opens"], m["Total Clicks"], m["BH"])
        out["pdf_file"] = pdf_name

        # Lead scoring (separate workflow, own folder).
        override = deps.get("lead_de_override")
        if override:
            de_name, de_key, de_cols = override
        else:
            de_name, de_key = resolve_lead_de(sfmc, send.get("lead_scoring_de", ""), identity.client)
            de_cols = de_ordered_columns(sfmc, de_key)
        de_rows = sfmc.get_de_rows_rest(de_key)
        out["lead_scoring_file"] = write_lead_scoring_csv(
            deps["lead_dir"], de_name, de_cols, de_rows, datetime.date.today())

        # Side effects are gated on needs_confirmation.
        if status == "needs_confirmation":
            out["status"] = "needs_confirmation"
            out["sheet"] = {"status": "skipped"}; out["calendar"] = {"status": "skipped"}
            out["drafts"] = {"status": "skipped"}
            return out

        # Sheet.
        plan = build_api_sheet_plan(send_obj, m["Total Opens"], m["BH"])
        out["flags"] += plan.flags
        written = sheet.write_send(deps["sheet_writer"], identity, plan,
                                  fill_blanks_only=not force)
        out["sheet"] = {"status": "forced" if force else "blanks-filled",
                        "cells": {h: f"{col_to_a1(c)}{r + 1}" for h, (r, c) in written.items()}}
        out["flags"] += plan.warnings

        # Calendar.
        if skip_calendar:
            out["calendar"] = {"status": "skipped"}
        else:
            mark = f"{datetime.date.today().month}/{datetime.date.today().day} {deps.get('mark_initials', 'JS')}"
            out["calendar"] = mark_calendar(deps["calendar_writer_for_tab"], send_obj.get("SentDate"),
                                            identity, mark, fill_blanks_only=not force)

        # Drafts (idempotent via manifest draft ids).
        out.setdefault("drafts", {})
        if skip_drafts:
            out["drafts"] = {"status": "skipped"}
        else:
            dw = deps["draft_writer"]
            if not out["drafts"].get("report"):
                out["drafts"]["report"] = dw.create_draft(build_report_draft(identity, folder))
            kdraft = build_kathryn_draft(identity, deps["lead_dir"] / out["lead_scoring_file"], hipaa)
            if kdraft is None:
                out["drafts"]["kathryn"] = None
                out["flags"].append("HIPAA - Kathryn notification skipped; PC routing not yet designed.")
            elif not out["drafts"].get("kathryn"):
                out["drafts"]["kathryn"] = dw.create_draft(kdraft)

        out["status"] = "complete"
    except Exception as exc:  # noqa: BLE001 - per-send isolation (spec §6)
        out["status"] = "failed"
        out["errors"].append(str(exc))
    return out
```

Now add the CLI (real adapters) at the end of `scripts/pull_reports.py`:

```python
def _load_dotenv(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _real_deps():
    reports_dir = Path(os.environ.get("REPORTS_DIR", str(Path.cwd().parent)))
    lead_dir = Path(os.environ.get("LEAD_SCORING_DIR", str(reports_dir / "Lead Scoring")))
    sfmc = SfmcClient(
        auth_base=os.environ["SFMC_AUTH_BASE_URL"], soap_base=os.environ.get("SFMC_SOAP_BASE_URL", ""),
        rest_base=os.environ.get("SFMC_REST_BASE_URL", ""), client_id=os.environ["SFMC_CLIENT_ID"],
        client_secret=os.environ["SFMC_CLIENT_SECRET"],
    )
    sheet_writer = sheets_writer.GoogleSheetsWriter(
        spreadsheet_id=os.environ.get("SHEET_ID"), tab=os.environ.get("SHEET_TAB", "Sheet1"),
        service_account=os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", "secrets/service-account.json"))
    cal_id = os.environ.get("CALENDAR_ID")

    def calendar_writer_for_tab(tab):
        return sheets_writer.GoogleSheetsWriter(
            spreadsheet_id=cal_id, tab=tab,
            service_account=os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", "secrets/service-account.json"))

    from tracking.gmail_source import GmailSource
    draft_writer = GmailSource(
        client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "secrets/client_secret.json"),
        token_path=os.environ.get("GOOGLE_TOKEN_PATH", "secrets/token.json"))
    return {
        "sfmc": sfmc, "reports_dir": reports_dir, "lead_dir": lead_dir,
        "sheet_writer": sheet_writer, "calendar_writer_for_tab": calendar_writer_for_tab,
        "draft_writer": draft_writer, "mark_initials": os.environ.get("CALENDAR_MARK_INITIALS", "JS"),
    }


def cmd_init(args):
    path = manifest_path(args.run_id or default_run_id())
    if path.exists():
        print(f"Manifest already exists: {path}")
        return 1
    save_manifest(path, scaffold_manifest(args.run_id or default_run_id()))
    print(f"Scaffolded {path} — fill in the run's sends, then `build`.")
    return 0


def cmd_build(args):
    rid = args.run_id or default_run_id()
    path = manifest_path(rid)
    manifest = load_manifest(path)
    deps = _real_deps()
    confirm_ids = set(args.confirm_zero or [])
    results = []
    for send in manifest["sends"]:
        if args.only and send.get("send_id") != args.only:
            results.append(send); continue
        out = run_send(send, deps, force=args.force, dry_run=args.dry_run,
                       skip_drafts=args.skip_drafts, skip_calendar=args.skip_calendar,
                       confirm_zero_ids=confirm_ids)
        results.append(out)
        print(f"{out.get('client')} {out.get('type')}: {out.get('status')}  "
              f"flags={len(out.get('flags', []))} errors={out.get('errors', [])}")
    if not args.dry_run:
        manifest["sends"] = results
        save_manifest(path, manifest)
    return 0


def cmd_status(args):
    path = manifest_path(args.run_id or default_run_id())
    manifest = load_manifest(path)
    for s in manifest["sends"]:
        print(f"- {s.get('client')} {s.get('type')} [{s.get('status', 'pending')}]")
        for f in s.get("flags", []):
            print(f"    flag: {f}")
        cal = s.get("calendar", {})
        for cand in cal.get("candidates", []):
            print(f"    calendar candidate: {cand}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pull_reports", description=__doc__)
    parser.add_argument("--run-id", help="manifest run id (default: current YYYY-MM)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="scaffold a run manifest").set_defaults(func=cmd_init)
    b = sub.add_parser("build", help="pull sends and produce the package")
    b.add_argument("--force", action="store_true", help="write non-blank sheet/calendar cells too")
    b.add_argument("--only", help="only this send_id")
    b.add_argument("--confirm-zero", action="append", help="release a needs_confirmation send_id")
    b.add_argument("--skip-drafts", action="store_true")
    b.add_argument("--skip-calendar", action="store_true")
    b.add_argument("--dry-run", action="store_true", help="compute plans; write nothing")
    b.set_defaults(func=cmd_build)
    sub.add_parser("status", help="print run status from the manifest").set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    _load_dotenv()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_pull_reports.py -k "require_hipaa or run_send" -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full new suite + confirm the baseline stays green**

Run: `./.venv/Scripts/python.exe -m pytest -m "not realdata" -q`
Expected: all pass (119 baseline + the new `test_pull_reports.py` tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/pull_reports.py tests/test_pull_reports.py
git commit -m "feat(pull_reports): orchestration + CLI (init/build/status, gates, dry-run)"
```

---

### Task 13: Operator docs point at the new tool

**Files:**
- Modify: `README.md`
- Modify: `docs/AUTOMATION_STATUS.md`

- [ ] **Step 1: Read the current operator-facing sections**

Use the Read tool on `README.md` and `docs/AUTOMATION_STATUS.md` (PowerShell alternative if needed: `Get-Content README.md -TotalCount 120`). Identify the section(s) that tell operators to use `sfmc-stage` / require the overview PDF (around README:60) and the AUTOMATION_STATUS "Not automated: ExactTarget export" line.

- [ ] **Step 2: Add a `pull_reports.py` section to `README.md`**

Insert a new subsection (after the existing CLI/SFMC section) with the real workflow:

```markdown
## Pulling reports directly from SFMC (`scripts/pull_reports.py`)

These sends are never emailed into Gmail, so pull them straight from Marketing
Cloud. Facts for a run live in `runs/<run-id>/manifest.json`; nothing is hardcoded.

    # 1. Scaffold this month's run and fill in the sends (send_id, booklet_selector,
    #    lead_scoring_de, hipaa) — hipaa is REQUIRED per send.
    ./.venv/Scripts/python.exe scripts/pull_reports.py init

    # 2. Preview without writing anything (reads APIs, mutates nothing):
    ./.venv/Scripts/python.exe scripts/pull_reports.py build --dry-run

    # 3. Build for real (CSVs + PDF + Lead Scoring + Print Status row + calendar + drafts):
    ./.venv/Scripts/python.exe scripts/pull_reports.py build

    # 4. See what happened (authoritative — reads the manifest):
    ./.venv/Scripts/python.exe scripts/pull_reports.py status

The overview PDF is reconstructed from the `Send` object + tracking events — the
old `sfmc-stage` / overview-PDF-required path is superseded. Sheet and calendar
writes are fill-blank-only; Gmail only ever creates drafts. A zero Unique
Opens/Clicks parks the send as `needs_confirmation` (release with
`--confirm-zero <send_id>`); HIPAA sends skip the Kathryn notification.
```

- [ ] **Step 3: Update `docs/AUTOMATION_STATUS.md`**

Change the line that lists ExactTarget export as "not automated" to reflect that direct SFMC pull is now implemented via `scripts/pull_reports.py`, and note the manifest is the source of truth. (Edit prose to match; keep the file's existing structure.)

- [ ] **Step 4: Sanity-check the CLI help matches the docs**

Run: `./.venv/Scripts/python.exe scripts/pull_reports.py --help && ./.venv/Scripts/python.exe scripts/pull_reports.py build --help`
Expected: subcommands `init/build/status` and flags `--force/--only/--confirm-zero/--skip-drafts/--skip-calendar/--dry-run` print as documented.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/AUTOMATION_STATUS.md
git commit -m "docs: point operators at pull_reports.py (supersedes sfmc-stage path)"
```

---

## Self-Review

**1. Spec coverage:**
- Rules vs facts (§2) → Global Constraints + manifest schema (Task 2), no hardcoded facts.
- Manifest spine + run-id (§3) → Tasks 2, 12; private via gitignore Task 1.
- Single script + reuse helpers (§4) → Tasks 2–12 build one file importing naming/sheet/sheets_writer/gmail_source/drafts.
- SFMC pull (§5.1) → Tasks 3, 4.
- Data-driven CSVs + core gate + optional flags (§5.2) → Task 6.
- Report PDF, no side-channel (§5.3) → Task 7.
- Lead Scoring separate + verbatim + Kathryn notification + HIPAA (§5.4) → Task 10; HIPAA-required enforced in Task 12.
- API→SheetPlan, reuse write_send not build_sheet_plan (§5.5) → Task 8.
- Calendar derived tab + candidates + fill-blank (§5.6) → Task 9 (`mark_calendar` scans **both** candidate tabs fully before deciding written/ambiguous/not-found; candidates carry tab/row/client/type + `searched_tabs`); candidate printing in `cmd_status` (Task 12).
- Report draft blank-To absolute attachments (§5.7) → Task 11.
- Idempotency/loud-failure/per-send isolation/PII (§6) → Task 12 `run_send` (try/except, manifest draft-id reuse, gitignored dirs).
- Config + in-scope repo changes (§7) → Task 1 (gitignore/deps/env/example) + Task 13 (docs).
- Dry-run non-mutation (§4, Rule 8) → Task 12 dry-run branch computes sheet cells + calendar tab/candidates + draft recipients/attachments via `_RecordingWriter`, asserting nothing is written (`test_run_send_dry_run_computes_plan_but_writes_nothing`).
- Zero-core gate blocks side effects (§5.2) → Task 12 + `test_run_send_needs_confirmation_skips_side_effects`.
- Out-of-scope items (§8) untouched: no send discovery, no HIPAA→PC, `sfmc.py` legacy left alone. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N"; all code steps contain full code. Docs Task 13 Step 3 edits existing prose (structure preserved) — acceptable, no code placeholder. ✓

**3. Type consistency:** `run_send(send, deps, *, ...)` signature matches its callers in `cmd_build`. `deps` keys (`sfmc`, `reports_dir`, `lead_dir`, `sheet_writer`, `calendar_writer_for_tab`, `draft_writer`, optional `lead_de_override`, `mark_initials`) are consistent between tests and `_real_deps`. `build_api_sheet_plan` returns `sheet.SheetPlan`; `write_send(writer, identity, plan, fill_blanks_only=...)` matches `tracking/sheet.py`. `DraftEmail(to,subject,body,attachments)` matches `tracking/drafts.py`. `mark_calendar` returns the dict shape consumed by `cmd_status`. `naming.finished_csv_name`/`finished_pdf_name`/`email_subject`/`REQUEST_FILE_DESCRIPTION`/`SendIdentity` all match `tracking/naming.py`. ✓

Note for the executor: `test_run_send_*` inject `lead_de_override` so lead-scoring runs without SOAP; the live path (`resolve_lead_de`/`de_ordered_columns`) is exercised in the operator dry-run/live check (Task 12 Step 5 is offline-only; live verification is the operator's `build --dry-run` then one real `build`, per spec §10).

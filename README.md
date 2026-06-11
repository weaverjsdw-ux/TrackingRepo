# Engagement Tracker — Current Automation

Downstream processing for Engagement Tracking Reports. The core library remains
credential-free and testable: given a folder of raw exports, it **identifies each
file by content**, **counts** the metric rows, computes the **BH** booklet
aggregation, parses the overview PDF, produces deterministic finished names, and
builds the Google Sheet write plan. The live edge is the CLI: Gmail pulls staged
attachments, Sheets writes the approved values, filing copies renamed
deliverables locally, and Gmail drafts report-delivery emails without sending.

## Run

```powershell
if (Test-Path .\engagement-tracker\pyproject.toml) { Set-Location .\engagement-tracker }
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
python tests\fixtures\_generate_synthetic.py   # (re)build synthetic fixtures
python -m pytest -q -m "not realdata"          # local/CI suite (no PII needed)
```

Operator commands:

```powershell
python -m tracking.cli pull
python -m tracking.cli write --commit
python -m tracking.cli draft-reports
python -m tracking.cli status
python -m tracking.cli run                     # scheduled pull + Sheet write
python -m tracking.cli run --drafts            # opt-in draft creation too
python -m tracking.cli sfmc-probe --send-id 12345
python -m tracking.cli sfmc-stage --send-id 12345 --client "Northshore College" --season Fall --year 2026 --type eNL
```

`draft-reports` uses `CONTACTS_CSV` (default `contacts.csv`; start from
`contacts.example.csv`) and records Gmail draft IDs in the local automation
state so reruns do not create duplicate drafts. The Gmail OAuth token must have
compose permission; rerun `authorize` if an older token only has intake access.

Scheduler wrapper:

```powershell
.\scripts\install_scheduled_task.ps1                 # hidden run every 4 minutes
.\scripts\install_scheduled_task.ps1 -Drafts         # only after contacts.csv is checked
.\scripts\install_scheduled_task.ps1 -IntervalMinutes 10
```

The scheduled task calls `scripts/run_scheduled_hidden.vbs`, which launches
`scripts/run_scheduled.ps1` with no visible console window. Power Automate can
wrap the same command for notifications, but should not own parsing, naming,
Sheet writes, or draft creation logic.

See `docs/AUTOMATION_STATUS.md` for the current automated/not-automated
boundary, live blockers, Power Automate search evidence, and review-gate status.

```python
from tracking import pipeline
r = pipeline.process_folder(r"...\<Client> - <Season> <Year> <Type>")
print(r.metrics)          # {"Total Sent": ..., "Unique Opens": ..., "BH": ..., ...}
print("\n".join(r.log))   # auditable per-run log
```

## Modules (`src/tracking/`)

| Module | Responsibility |
|---|---|
| `naming.py` | **One** source-of-truth namer: `(client, season, year, type, description) -> "Client Season Year Type - Description"`. Parses identity from the folder name. Em-dash email subject. |
| `parse.py` | Tolerant CSV reader (BOM / quoted commas / embedded newlines / diacritics). Metric value = **data row count**. Link normalization + per-link counts. |
| `identify.py` | **Content-based** identification by header signature; the two identical-header click exports are split by distinct-link count (many → master Unique Clicks; one → request/booklet file). Unknown file → fail loud. |
| `bh.py` | Two live paths: **request-file primary** (BH = its row count) and **clicks-derive fallback** (booklet = common parent of the `/article-N` links; article/system/CTA links excluded). Both present & disagree → use request file + warn. Fail loud on zero/ambiguous; logs the chosen link each run. |
| `pipeline.py` | Orchestrates identify → count → name → BH into a **non-destructive** dry-run plan + log. Lead scoring is ignored (out of scope). |
| `intake.py` (Phase 2) | Source-agnostic Gmail intake: group report emails by JobID → stage to a drop folder → dedup → process → mark-processed → move to `processed/`. The Gmail wire protocol sits behind the `EmailSource` interface (testable with a fake; no creds). |
| `overview.py` (Phase 3) | Parse the overview-PDF Summary into expected per-metric totals (used for the cross-check). |
| `sheet.py` (Phase 3) | `build_sheet_plan` (loud on missing-but-expected metric / unmatched bounce / BH=0; flags fallback BH; warns on snapshot drift) + `write_send` (match client row × metric-header column; loud on missing row/column). |
| `contacts.py` / `drafts.py` | Local CSV contact validation + Gmail draft construction. Missing, disabled, or ambiguous contacts block drafts. Official overview PDF attachment is required. |
| `run_state.py` | Local automation state: last run, processed sends, pending JobIDs with first/last seen timestamps, and draft IDs for idempotency. |
| `sfmc.py` | API-first ExactTarget/SFMC feasibility gate and source-staging helpers. If the API cannot provide the official overview PDF, the PDF remains required and a UI/Power Automate fallback is only for that missing artifact. |
| `gmail_source.py` / `sheets_writer.py` | Live adapters behind the `EmailSource` / `DraftWriter` / `SheetWriter` interfaces (optional `[gmail]`/`[sheets]` extras, lazy imports). Offline tests use fakes; live runs require local credentials. |

## Fixtures / PII

- **Synthetic, committed** (`tests/fixtures/synthetic/…`): real header signatures + synthetic rows, with the quirks baked in (BOM, embedded comma, embedded newline, diacritics). These reproduce the exact counts and link mix and run in CI.
- **Real, git-ignored**: the `Bradley University - Spring 2026 eNL/` folder lives **outside** this repo; `realdata`-marked tests run against it locally and **skip when absent**. No PII in the repo or CI, ever (`.gitignore` is belt-and-suspenders on top of that).

## 5-line writeup

1. **Approach** — small single-responsibility modules; one source-of-truth namer; identify-by-content (never filename); metric = row count; BH via request-file primary + clicks-derive fallback; everything locked by golden tests (synthetic in CI, real git-ignored, skip-when-absent).
2. **Two-sends fixture** — the example folder holds the FINISHED files and the RAW exports of ONE clean send side by side; the raw exports were pulled slightly later, so a few counts differ (Opens 2138/2146, Clicks 179/180, Unsub 29/32). Each file is asserted against its OWN count — never forced raw == finished.
3. **BH two paths** — finished send: request file present → BH = its row count (21). Raw send: no request file → derive from the master, booklet = the `/enewsletter/giving-thought-spring-2026` landing page (common parent of `/article-N`), excluding article/system/CTA(`/requestguide`) links → 21. Both present & disagreeing → use the request file + warn.
4. **Parser quirks hit** — UTF-8 BOM, quoted embedded commas, embedded newlines, diacritics, and per-recipient `utm_/sfmc_id` query params on click URLs (stripped before grouping).
5. **Harden next** — (a) month-scale unattended run evidence; (b) live PC/client draft acceptance; (c) lead-scoring/HIPAA workflow after engagement tracking is stable; (d) multi-booklet sends currently fail loud by design; (e) confirm the "Request Your" label doesn't vary by client.

## Phase 2 — Gmail intake (live-verified)

How reports actually arrive (confirmed against the live inbox): ExactTarget
sends one "Email Export" notification per file (body carries `Exported Type` and
`JobID`), and the operator's "Tracking Export" email carries the overview PDF.
So a send is assembled by **JobID** (all of a send's emails share one), with
identity read from the overview PDF's `Name` field and subject parsing as a
fallback. `intake.py` groups by JobID, stages + content-hash dedups attachments,
runs the pipeline, gates on completeness, then marks messages processed (removes
the queue label) and moves the send to `processed/`. Verified live: it pulled
the real inbox and assembled the Bradley send (JobID 687422).

Locked: **OAuth user-consent**; identity from the overview PDF; the two
identical "click" exports are told apart by distinct-link count (master vs
request); mark-processed = **remove the label**; completeness = core (Total Sent
+ Unique Opens + Unique Clicks + overview PDF).

## Phase 3 — Sheet write-back (live-verified)

Mapped to the live `2026 Print Status Report` tab (headers on row 3; Client in
col B). Writes these mapped columns: `# Total sent`, `# Delivered` (PDF),
`# Unique clicks`, `Booklet landing page unique clicks` (= BH, the request
export), `# Total opens` (PDF), `# Unique opens`, `Unique open rate %`, `Unique
click-through %`, and `Subject line`. Safety:
- **row match** = Client + Type; if >1, tiebreak by the AJ send-date month →
  season; still ambiguous → **fail loud** with the candidate rows;
- **fill blanks only** — a cell already holding a different value is skipped and
  flagged, never overwritten;
- a metric the **overview PDF** reports nonzero but whose source is absent **fails
  loud** (no silent 0); **BH == 0 fails loud**; BH via the clicks-derive fallback
  is **flagged**; file-vs-PDF drift **warns** and writes the file count.

Verified by a real write to the trial copy: Bradley eNL → **row 636**. Adapter:
`sheets_writer.GoogleSheetsWriter` (`pip install -e .[sheets]`).

## Repo & CI

Local git repo initialized; **CI** (`.github/workflows/ci.yml`) runs
`pytest -m "not realdata"` on push/PR — the synthetic, PII-free subset. The real
example folder is git-ignored and lives outside the repo, so CI never sees PII.
**Use a private remote only.** Definition-of-done practiced via the red→green
commit pair for Phase 3.

## Out of scope / deprecated

The §2.E Click-Activity / `cID=` workflow is **retired and must not be rebuilt**;
`tests/test_deprecated_absent.py` fails if it reappears.

PC/client report delivery and lead-scoring/HIPAA handling are intentionally
split: engagement-report delivery is draft-only automation, while lead scoring
remains deferred. Power Automate should stay a thin wrapper/notification surface
around `python -m tracking.cli run` unless it adds clear operator value.

# Engagement Tracker — Phase 1 (trial)

Downstream processing for Engagement Tracking Reports. Phase 1 is folder-based
and credential-free (per `AUTOMATION_BRIEF.md` §6): given a folder of raw
exports, it **identifies each file by content**, **counts** the metric rows,
computes the **BH** booklet aggregation, and produces the **deterministic
finished names** + the values that would go into the Sheet — as a reversible
dry-run plan, with golden-file tests.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe tests\fixtures\_generate_synthetic.py   # (re)build synthetic fixtures
.\.venv\Scripts\python.exe -m pytest -q                            # full suite
.\.venv\Scripts\python.exe -m pytest -q -m "not realdata"          # CI subset (no PII needed)
```

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
| `intake.py` (Phase 2) | Source-agnostic Gmail intake: group labeled emails by send → stage to a drop folder → dedup → process → mark-processed → move to `processed/`. The Gmail wire protocol sits behind the `EmailSource` interface (testable with a fake; no creds). |
| `overview.py` (Phase 3) | Parse the overview-PDF Summary into expected per-metric totals (used for the cross-check). |
| `sheet.py` (Phase 3) | `build_sheet_plan` (loud on missing-but-expected metric / unmatched bounce / BH=0; flags fallback BH; warns on snapshot drift) + `write_send` (match client row × metric-header column; loud on missing row/column). |
| `gmail_source.py` / `sheets_writer.py` | Live adapters behind the `EmailSource` / `SheetWriter` interfaces (optional `[gmail]`/`[sheets]` extras, lazy imports). **Structural-only until run live.** |

## Fixtures / PII

- **Synthetic, committed** (`tests/fixtures/synthetic/…`): real header signatures + synthetic rows, with the quirks baked in (BOM, embedded comma, embedded newline, diacritics). These reproduce the exact counts and link mix and run in CI.
- **Real, git-ignored**: the `Bradley University - Spring 2026 eNL/` folder lives **outside** this repo; `realdata`-marked tests run against it locally and **skip when absent**. No PII in the repo or CI, ever (`.gitignore` is belt-and-suspenders on top of that).

## 5-line writeup

1. **Approach** — small single-responsibility modules; one source-of-truth namer; identify-by-content (never filename); metric = row count; BH via request-file primary + clicks-derive fallback; everything locked by golden tests (synthetic in CI, real git-ignored, skip-when-absent).
2. **Two-sends fixture** — the example folder holds the FINISHED files and the RAW exports of ONE clean send side by side; the raw exports were pulled slightly later, so a few counts differ (Opens 2138/2146, Clicks 179/180, Unsub 29/32). Each file is asserted against its OWN count — never forced raw == finished.
3. **BH two paths** — finished send: request file present → BH = its row count (21). Raw send: no request file → derive from the master, booklet = the `/enewsletter/giving-thought-spring-2026` landing page (common parent of `/article-N`), excluding article/system/CTA(`/requestguide`) links → 21. Both present & disagreeing → use the request file + warn.
4. **Parser quirks hit** — UTF-8 BOM, quoted embedded commas, embedded newlines, diacritics, and per-recipient `utm_/sfmc_id` query params on click URLs (stripped before grouping).
5. **Harden next** — (a) bounce sub-typing (Hard/Soft/Block share a header — needs a Bounce-Reason content rule); (b) multi-booklet sends (≤3 links) currently fail loud by design; (c) overview-PDF value extraction; (d) a completeness gate (which files make a send "ready") to drive Phase 2 pending/delayed handling; (e) confirm the "Request Your" label doesn't vary by client.

## Phase 2 — Gmail intake (live-verified)

How reports actually arrive (confirmed against the live inbox): ExactTarget
sends one "Email Export" notification per file (body carries `Exported Type` and
`JobID`), and the operator's "Tracking Export" email carries the identity in its
subject (`<Client> <Season> <Year> <Type> - Engagement Tracking Report`) plus the
overview PDF. So a send is assembled by **JobID** (all of a send's emails share
one), with identity from the overview-PDF email. `intake.py` groups by JobID,
stages + content-hash dedups attachments, runs the pipeline, gates on
completeness, then marks messages processed (removes the label) and moves the
send to `processed/`. Verified live: it pulled the real inbox and assembled the
Bradley send (JobID 687422).

Locked: **OAuth user-consent**; identity from the overview-PDF subject; the two
identical "click" exports are told apart by distinct-link count (master vs
request); mark-processed = **remove the label**; completeness = core (Total Sent
+ Unique Opens + Unique Clicks + overview PDF).

## Phase 3 — Sheet write-back (live-verified)

Mapped to the live `2026 Print Status Report` tab (headers on row 3; Client in
col B). Writes six columns: `# Total sent`, `# Delivered` (PDF), `# Unique
clicks`, `Booklet landing page unique clicks` (= BH, the request export),
`# Total opens` (PDF), `# Unique opens`. Safety:
- **row match** = Client + Type; if >1, tiebreak by the AJ send-date month →
  season; still ambiguous → **fail loud** with the candidate rows;
- **fill blanks only** — a cell already holding a different value is skipped and
  flagged, never overwritten;
- a metric the **overview PDF** reports nonzero but whose source is absent **fails
  loud** (no silent 0); **BH == 0 fails loud**; BH via the clicks-derive fallback
  is **flagged**; file-vs-PDF drift **warns** and writes the file count.

Verified by a real write to the trial copy: Bradley eNL → **row 636**, all six
cells. Adapter: `sheets_writer.GoogleSheetsWriter` (`pip install -e .[sheets]`).

## Repo & CI

Local git repo initialized; **CI** (`.github/workflows/ci.yml`) runs
`pytest -m "not realdata"` on push/PR — the synthetic, PII-free subset. The real
example folder is git-ignored and lives outside the repo, so CI never sees PII.
**Use a private remote only.** Definition-of-done practiced via the red→green
commit pair for Phase 3.

## Out of scope / deprecated

The §2.E Click-Activity / `cID=` workflow is **retired and must not be rebuilt**;
`tests/test_deprecated_absent.py` fails if it reappears.

# Design — Manifest-driven SFMC report pull (`pull_reports`)

**Date:** 2026-07-15
**Status:** Approved (2026-07-15); transitioning to the implementation plan.
**Author/operator:** John Weaver (Pentera). Solo-operated automation.

---

## 1. Problem

For each engagement send on the Tracking Reports calendar we must produce a report
package (a summary/overview and per-metric subscriber CSVs), write the numbers into the
`2026 Print Status Report`, mark the calendar, draft the delivery email, and — separately
— export the client's Lead Scoring data and notify for upload. These sends are **not**
emailed into Gmail, so the existing Gmail-intake pipeline never sees them; the data must
be pulled directly from Salesforce Marketing Cloud / ExactTarget (SFMC).

The SFMC API path **already works** and is proven (OAuth client-credentials → SOAP 1.1
Retrieve for the `Send` object and tracking events; REST rowset for data extensions). The
real problem is **cohesion**, not capability. Prior work produced *successful one-off
runs* whose truth was scattered across: committed code, uncommitted changes, temporary
scratch scripts, `%TEMP%\sfmc_plans.json`, `Completed Reports\`, `drop\processed\`, Gmail
drafts, and stale docs. Those scripts also baked one run's incidental facts into the
logic — a fixed month ("July 2026"), six specific sends, five calendar cells, hardcoded
SendIDs, booklet tags, and folder lists. Those are artifacts of one run, not rules.

**This design makes the tool cohesive and manifest-driven** so that next month's run does
not inherit this month's facts, and "what happened" lives in exactly one authoritative
place.

## 2. Core principle: rules vs. facts

The single organizing idea:

- **Rules** are invariant and live in **code**. They never name a month, a client, a
  SendID, a booklet tag, or a count.
- **Facts** are per-run and live in **one manifest file**. The tool reads facts from it
  and writes results back to it.

### Rules (in code, never parameterized by a run)

1. One report package per selected send; one summary/report PDF per send.
2. Engagement CSVs are **data-driven** — the file set follows the metrics that have data.
3. **Core** engagement metrics (Total Sent, Unique Opens, Unique Clicks) must be retrieved
   successfully. Total Sent = 0 fails the send loud (a zero-recipient pull is broken); a
   zero Unique Opens or Unique Clicks puts the send in **`needs_confirmation`** and
   **blocks all its side effects** (sheet, calendar, drafts) until the operator confirms
   (§5.2). **Optional** metrics (Hard/Soft/Block Bounces, Unsubscribes, booklet) may be
   zero/absent, but must be **flagged** (and the empty file is skipped).
4. Lead Scoring is a **separate workflow**, saved verbatim-style as
   `sd_<Client> - Lead Scoring<YYYYMMDD>.csv` (never renamed).
5. Sheet and calendar writes are **fill-blank-only** unless explicitly `--force`d.
6. Gmail **creates drafts only, never sends**.
7. **HIPAA is a required per-send fact** (no default). If `hipaa` is absent for a send, that
   send fails loud. HIPAA sends export the data file but **never** trigger the Kathryn
   notification — it is skipped and flagged (PC routing is out of scope).
8. **`--dry-run` mutates nothing** — no files, drafts, sheet/calendar writes, or per-send
   manifest results (§4).

### Facts (only in the manifest, never in code)

Which sends are in the run; run-id / month; number of sends; send IDs; booklet
selector/tag; Lead Scoring DE name/key; HIPAA flag; resolved send dates; output folders;
sheet row/cells; calendar tab/cell; draft IDs.

## 3. The manifest (the spine)

**Location:** `engagement-tracker/runs/<run-id>/manifest.json`.
**Run-id:** defaults to the current month `YYYY-MM` (e.g. `2026-07`); overridable with
`--run-id`. Re-running **updates the same manifest in place** (idempotent enrichment).

Run-id is only the *batch label for when the work was done*. It is independent of any
send's date: a July run may process late-June sends, and each send's own `send_date`
(resolved from the `Send` object) drives its calendar tab. This is the rules/facts split
made concrete.

### Schema

```jsonc
{
  "run_id": "2026-07",
  "created": "2026-07-15",
  "sheet":    { "id": "<SHEET_ID>", "tab": "<SHEET_TAB>" },
  "calendar": { "id": "1eTZXc9bNaRbMWmFeJPPc56LCmbO42egvkJy1Sjo89is", "mark_initials": "JS" },
  "sends": [
    {
      // ── operator-authored facts (input) ──
      "client": "Yale New Haven Hospital",
      "season": "Spring", "year": "2026", "type": "eNL",
      "send_id": "691994",
      "booklet_selector": "v=enlA",        // type-rule prefills; operator confirms
      "lead_scoring_de": "sd_Yale New Haven Hospital - Lead Scoring", // optional; else derived
      "hipaa": false,                      // REQUIRED per send — no default; absent → send fails loud
      "confirm_zero": false,               // set true (or pass --confirm-zero <id>) to release a needs_confirmation send

      // ── tool-resolved results (output, written back) ──
      "send_date": "2026-06-…",
      "output_folder": "Completed Reports/Yale New Haven Hospital - Spring 2026 eNL",
      "metrics":   { "Total Sent": 0, "Unique Opens": 0, "Unique Clicks": 0, "BH": 0,
                     "Hard Bounces": 0, "Soft Bounces": 0, "Block Bounces": 0, "Unsubscribes": 0,
                     "Total Opens": 0, "Total Clicks": 0, "Delivered": 0 },
      "csv_files": [ "… - Total Sent.csv", "… - Unique Opens.csv", "…" ], // data-driven
      "pdf_file":  "… - Engagement Tracking Report.pdf",
      "lead_scoring_file": "sd_Yale New Haven Hospital - Lead Scoring20260715.csv",
      "sheet":    { "row": 17, "cells": { "# Total sent": "…!AB17" }, "status": "blanks-filled" },
      "calendar": { "tab": "June 2026", "cell": "Y17", "status": "written" },
      "drafts":   { "report": "<draft_id>", "kathryn": "<draft_id|null>" },
      "flags":    [ "Block Bounces: 0 — no file written" ],
      "status":   "complete",              // complete | needs_confirmation | partial | failed
      "errors":   []
    }
  ]
}
```

The manifest also provides **draft idempotency for free**: a re-run skips any draft whose
id is already recorded (and still exists in Gmail), so re-runs never pile up duplicate
drafts. There is **no `%TEMP%` state** — the PDF and sheet values are computed live from
the `Send` object + events on every run.

**The manifest is private.** It holds client names, SendIDs, counts, sheet cells, local
paths, and Gmail draft IDs, so `runs/` is git-ignored (§7); only a redacted
`runs/example/manifest.example.json` is committed as the schema reference.

## 4. Architecture

**One saved script:** `engagement-tracker/scripts/pull_reports.py`, run with the repo's
venv Python. It **contains** the SFMC SOAP/REST client and the reportlab PDF, and
**imports** the package's already-solid, tested helpers rather than re-implementing them:

- `tracking.naming` — every output filename + the email subject (source-of-truth names).
- `tracking.sheet` (`SheetPlan`, `write_send`) + `tracking.sheets_writer.GoogleSheetsWriter`
  — the safe Print Status write (client+type match, season tiebreak, fill-blank-only).
- `tracking.gmail_source.GmailSource.create_draft` + `tracking.drafts.DraftEmail` — drafts.

Internally the script is split into clearly-labelled sections so concerns don't tangle and
future file-splits have clean seams: **SFMC client · engagement CSVs · report PDF ·
lead-scoring (self-contained) · sheet · calendar · drafts · manifest I/O**. The
lead-scoring and SFMC-client sections are the natural first extractions if the file grows.

The existing REST-probe `src/tracking/sfmc.py` is a **different, non-working path**; it is
**left untouched** and noted as legacy. This script is the authoritative SFMC path.

### Commands (argparse subcommands)

- **`init`** — scaffold `runs/<run-id>/manifest.json` with a template send entry; the
  type-rule prefills `booklet_selector`. The operator fills in the run's facts.
- **`build`** — read the manifest; for each send run the pipeline (§5); write results back
  to the manifest. Flags: `--force` (write non-blank cells too), `--only <send_id>`,
  `--confirm-zero <send_id>` (release a `needs_confirmation` send's side effects; equivalent
  to setting `confirm_zero: true`), `--skip-drafts`, `--skip-calendar`, `--dry-run`.
- **`status`** — print purely from the manifest. This is the authoritative "what happened."

**`--dry-run` semantics (mutates nothing).** A dry run reads the SFMC/Sheet/Calendar APIs
and computes the full plan for each send — the CSV set it *would* write, the sheet row +
cells it *would* fill, the calendar tab + cell (with **all candidate blocks** listed when
not found/ambiguous, §5.6), and the draft recipients + attachments — then **writes
nothing**: no CSV/PDF/lead files, no drafts, no sheet or calendar updates, and no per-send
`sends[]` results in the manifest. (Optional `--save-plan` may write a clearly-labelled
top-level `last_dry_run` block for `status` to show; it never touches per-send results.)

## 5. The per-send pipeline (one API pull → outputs)

For each send, authenticate once and pull the `Send` object + tracking events, then:

### 5.1 SFMC pull (verified working)

- **Auth:** OAuth `client_credentials` POST to `{SFMC_AUTH_BASE_URL}/v2/token`; capture
  `access_token`, `soap_instance_url`, `rest_instance_url`.
- **SOAP 1.1** (not 1.2 / WS-Addressing — that 500s): envelope
  `xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"`, token in a
  `<fueloauth xmlns="http://exacttarget.com">` header, POST to `{soap}/Service.asmx`,
  `SOAPAction: Retrieve`; page via `ContinueRequest` while
  `OverallStatus=MoreDataAvailable`.
- **`Send`** (filter `ID`): `ID, EmailName, Subject, SentDate, NumberSent,
  NumberDelivered, UniqueOpens, UniqueClicks, HardBounces, SoftBounces, OtherBounces,
  Unsubscribes`. The overview PDF is not needed.
- **Events** (filter `SendID`): `SentEvent`, `OpenEvent`, `ClickEvent(+URL)`,
  `BounceEvent(+BounceCategory, SMTPReason)`, `UnsubEvent`.

The `Send` object supplies the **authoritative aggregates** (used for the PDF and the
sheet). Event row-lists supply the **subscriber-level CSVs**. The two can differ by a few
rows (snapshot timing) — this is expected and matches prior observation; do not force
them equal.

### 5.2 Engagement CSVs — data-driven (Rule 2/3)

Into `Completed Reports/<folder_name>/`, named via `naming.finished_csv_name(identity,
description)` and `naming.REQUEST_FILE_DESCRIPTION` for the booklet. Each row is
`Subscriber Key, Email Address (= SubscriberKey in this account), <event detail>`; dedup by
SubscriberKey (keep earliest EventDate); dates rendered US-style `M/D/YYYY h:mm AM/PM`.

- **Core (required):** Total Sent, Unique Opens, Unique Clicks. Total Sent = 0 → fail the
  send (`status: failed`). Zero Unique Opens or Unique Clicks → `status: needs_confirmation`:
  the local CSVs/PDF are still written for inspection, but the **shared side effects — sheet,
  calendar, and drafts — are skipped** for that send until it is released via
  `confirm_zero: true` or `--confirm-zero <send_id>`.
- **Optional (0/absent → record 0, flag, skip empty file):** Hard Bounces, Soft Bounces,
  Block Bounces, Unsubscribes, Request Your (booklet).
- **Booklet / BH:** `BH = ` count of deduped clicks whose URL contains
  `booklet_selector`. If `BH == 0`, flag it and **omit** the booklet cell from the sheet
  plan (the sheet layer forbids writing a 0 booklet value).
- **Bounce split:** `BounceCategory` prefix → hard / soft; everything else → block. Known
  caveat: this sub-split will not byte-match the `Send` object's Hard/Soft/Other split
  (ET disagrees with itself); the **total is exact**, the who-bounced list is correct.
  The PDF footer states this.

Record every metric's count in `manifest.metrics`; record the files actually written in
`manifest.csv_files`.

### 5.3 Report PDF (Rule 1)

`naming.finished_pdf_name(identity)` into the same folder. The styled EKU-template layout
(KPI tiles, Send/Open performance, Inbox Activity, Unengaged, Delivery Funnel), rendered
with reportlab. **All values come from the `Send` object + event counts + BH** — there is
**no `sfmc_plans.json` side-channel** (removing that dependency is a primary goal; it is
what made the old generator unrepeatable). Total Opens = `len(OpenEvent)`, Total Clicks =
`len(ClickEvent)`.

### 5.4 Lead Scoring — separate workflow (Rule 4)

Self-contained section; **not** tangled into engagement CSV generation.

- **Source:** the client's `sd_<Client> - Lead Scoring` data extension. `lead_scoring_de`
  from the manifest (name or CustomerKey); if omitted, derive `sd_<Client> - Lead Scoring`
  and fail loud if not found or ambiguous (some DE names differ from the send client — e.g.
  "University of Tennessee Institute for Public Service" → `sd_East Tennessee Foundation -
  Lead Scoring` — so the explicit field is the robust path).
- **Read:** REST rowset `{rest}/data/v1/customobjectdata/key/{customerKey}/rowset`
  (paged). This is preferred over SOAP `DataExtensionObject[...]`, which silently returns
  0 rows if any requested field is non-retrievable. Column order via
  `DataExtensionField.Ordinal`.
- **Save:** `TRACKINGREPORTS/Lead Scoring/` (flat; filenames self-identify). Filename =
  `<resolved DE name><YYYYMMDD>.csv` (verbatim style; never renamed).
- **Kathryn draft:** a Gmail draft to `kathryn.baugh@pentera.com` with the **Lead Scoring
  CSV attached** (absolute path, MAX_PATH-safe). The body only states the file is attached
  and ready for Client Access upload — it does **not** dump the local path/location.
  Records the draft id in the manifest. *(Amended 2026-07-23: was notification-only with the
  path in the body and no attachment; John changed it to attach the actual file.)*
- **HIPAA — `hipaa` is a REQUIRED per-send fact; if absent the send fails loud** (never
  assume non-HIPAA). When `true`, **skip** the Kathryn draft and attachment entirely and
  **flag** ("HIPAA — PC routing not yet designed"); the data-file export itself still runs.
  The HIPAA→PC delivery branch is out of scope.

### 5.5 Print Status Report write (Rule 5)

**Construct the `sheet.SheetPlan` directly** from the API aggregates below, then call
`sheet.write_send(writer, identity, plan)` (header row index 2, Client+Type match, season
tiebreak, `fill_blanks_only` unless `--force`). **Do not reuse `build_sheet_plan`** — it is
oriented to PDF/file-count sources (its `COLUMN_SOURCES` kinds are `pdf`/`file`) and would
shoehorn API aggregates into the wrong assumptions. Only `write_send`'s row-matching and
fill-blank logic is reused. Values sourced from the API:

| Sheet header | Source |
|---|---|
| `# Total sent` | `Send.NumberSent` |
| `# Delivered` | `Send.NumberDelivered` |
| `# Unique opens` | `Send.UniqueOpens` |
| `# Unique clicks` | `Send.UniqueClicks` |
| `# Total opens` | `len(OpenEvent)` |
| `Booklet landing page unique clicks` | `BH` (omitted if 0) |
| `Unique open rate %` | `UniqueOpens / Delivered` (decimal, round 5) |
| `Unique click-through %` | `UniqueClicks / Delivered` (decimal, round 5) |
| `Subject line` | `Send.Subject` |

Record the matched `row` and written `cells` + a `status` (`blanks-filled` / `skipped` /
`forced`) in the manifest.

### 5.6 Calendar mark (Rule 5; tab derived, never hardcoded)

Service-account write (the `...iam.gserviceaccount.com` address, now Editor) to the
Tracking Reports calendar (`1eTZXc9bNaRbMWmFeJPPc56LCmbO42egvkJy1Sjo89is`).

- **Tab:** derived from `send_date`. Because the report task lags the send by ~2 weeks,
  search the send-date month **and the following month** for the block.
- **Cell:** find the client+type day-block (name columns `[0,5,10,15,20]`, type at `+1`),
  target the "Engagement Reports downloaded on/by" cell at name-col `+4`. Fill only if
  blank; mark `<run-date M/D> <initials>` (e.g. `7/15 JS`).
- **Not found / ambiguous → flag, do not guess.** Record resolved `tab` + `cell` +
  `status` in the manifest, and in `status` / `--dry-run` **print every candidate block**
  considered (tab, client, type, date-context, row) — not merely "flagged" — so the
  operator can resolve it by hand.

### 5.7 Report delivery draft (Rule 6)

A Gmail draft via `create_draft(DraftEmail(...))`: `to=[]` (blank — operator fills
recipients), empty body, subject `naming.email_subject(identity)`, attachments = the
folder's PDF + all written metric CSVs, referenced by **absolute path** (a relative
`../Completed Reports` path plus a long client name exceeds Windows MAX_PATH 260 and the
file is silently skipped). Records the draft id in the manifest.

## 6. Correctness & safety

- **Idempotency:** CSVs / PDF / lead CSV overwrite; sheet + calendar are fill-blank-only;
  drafts skip when already recorded in the manifest. A `build` re-run is safe.
- **Loud failure, no silent fallback:** a missing required metric, an unresolved/ambiguous
  sheet row, an ambiguous Lead Scoring DE, or a missing credential fails that send loudly
  (`status: failed`, `errors[]`), and the run continues to the next send. Never write a
  wrong or placeholder number.
- **Per-send isolation:** each send is wrapped independently; one failure does not abort
  the batch.
- **PII/PHI:** Subscriber CSVs (SubscriberKey = email) and Lead Scoring data are sensitive.
  Files land only in the gitignored report / `Lead Scoring/` folders; no cell values are
  logged; the Kathryn draft attaches only the Lead Scoring CSV (a Gmail draft, never sent);
  HIPAA sends skip Kathryn entirely.

## 7. Configuration and in-scope repo changes

**Env (`.env`, already present):** `SFMC_AUTH_BASE_URL`, `SFMC_REST_BASE_URL`,
`SFMC_SOAP_BASE_URL`, `SFMC_CLIENT_ID`, `SFMC_CLIENT_SECRET`, `SHEET_ID`, `SHEET_TAB`,
`GOOGLE_SHEETS_SERVICE_ACCOUNT`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_TOKEN_PATH`,
`REPORTS_DIR`. New (optional, with defaults): `LEAD_SCORING_DIR` (default
`<REPORTS_DIR>/Lead Scoring`), `CALENDAR_ID`, `CALENDAR_MARK_INITIALS` (default `JS`).

Because the prior scatter is the problem this build fights, these repo changes are **in
scope for this build**, not follow-ups:

- **`.gitignore` hardening:** add `runs/` (the manifest holds PII) with a
  `!runs/example/manifest.example.json` exception; and add the **missing `* eQC - *.csv`**
  pattern — only `eNL`/`ePC` metric CSVs are ignored today, so eQC files (Monmouth, Mount
  Vernon, Alaska, …) would leak. Lead Scoring files are already covered by `sd_*.csv`.
  **Sequencing constraint:** this hardening must land **before** any command that can create
  `runs/` or write report/lead CSVs — it is the first implementation step, not a later one.
- **`pyproject.toml`:** add `reportlab>=4.0` (a new `reports` extra, or a core dep). The
  Calendar API reuses `google-api-python-client` + `google-auth`, already in the `sheets`
  extra.
- **`.env.example`:** add `SFMC_SOAP_BASE_URL`, `CALENDAR_ID`, `LEAD_SCORING_DIR`,
  `CALENDAR_MARK_INITIALS`, and un-comment/reframe `SHEET_ID` / `GOOGLE_SHEETS_SERVICE_ACCOUNT`
  and the SFMC block away from the old probe/stage framing.
- **Docs:** update `README.md` and `docs/AUTOMATION_STATUS.md` so operators are pointed at
  `pull_reports.py` (manifest-driven, PDF reconstructed from the API), not the old
  `sfmc-stage` / overview-PDF-required path.
- **`runs/example/manifest.example.json`:** a redacted example manifest committed as the
  schema reference.

## 8. Out of scope / deferred

- Calendar-driven **send discovery** (operator supplies the send list in the manifest).
- The **HIPAA → PC** lead-scoring delivery branch (needs a per-client HIPAA + PC-email
  source). HIPAA sends are skipped-and-flagged until then.
- Rewriting the legacy REST-probe `sfmc.py`.
- Donor-profile columns on engagement CSVs (confirmed not required).

## 9. Follow-ups (not this build)

- Reconcile the remaining stale doc `AUTOMATION_BRIEF.md` (still says "no ExactTarget"), and
  curate the pre-existing uncommitted working-tree changes + untracked
  `SFMC_INTEGRATION_PLAN.md` — the same scatter this design fights, but outside this build's
  commit scope (§11). (README + `AUTOMATION_STATUS.md` are updated *in* this build, §7.)
- Consider graduating the SFMC client + manifest sections into package modules (with tests)
  once the single-script version is proven over a full month.

## 10. Verification approach

- **Offline:** unit-test the pure logic that has no network — manifest read/write/enrich,
  the data-driven CSV selection (core-required / optional-flagged / booklet-zero), the
  **zero-core gate** (`needs_confirmation` blocks side effects; `--confirm-zero` releases),
  the **HIPAA-required guard** (absent → fail; true → Kathryn skipped), US date formatting,
  bounce split, calendar tab derivation + candidate listing, and the API-values →
  `SheetPlan` mapping — using synthetic `Send`/event fixtures. Keep the suite
  credential-free. Baseline today is green (**119 passed**); the new tests extend it and
  must keep CI (`pytest -m "not realdata"`) green.
- **Live (operator):** a `--dry-run build` prints planned files, sheet cells, calendar
  cell, and draft recipients/attachments without writing; then a real `build` on one send
  end-to-end, verified against the known-good numbers (the six sends already pulled tie out
  to the `Send` object exactly; booklet counts match the known BH values).

## 11. Approval scope

This spec is the only artifact approved here. Approval explicitly **does not** cover the
pre-existing dirty working-tree changes on `automation-followups-consolidated`
(`src/tracking/filing.py`, `tests/test_cli.py`, `tests/test_filing.py`,
`drop/.intake_state.json`) or the untracked `docs/SFMC_INTEGRATION_PLAN.md`; those are
unrelated and are curated separately (§9). The spec commit touches only this design file.

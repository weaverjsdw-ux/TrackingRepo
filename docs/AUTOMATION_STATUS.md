# Tracking Reports Automation Status

Last reviewed: 2026-07-17

## Automated Now

- Gmail intake groups ExactTarget export emails by JobID, stages attachments,
  deduplicates by content hash, processes complete sends, removes the intake
  label, and moves completed sends to `drop/processed`.
- Parser/pipeline identifies report files by content, ignores lead scoring,
  counts metrics, resolves BH, parses the overview PDF, and creates the Sheet
  write plan.
- Sheet write-back updates the configured Google Sheet with fill-blanks-only
  safety by default.
- Filing creates or repairs the renamed report folder for each processed send.
- Gmail report delivery creates drafts only, never sends, using `contacts.csv`
  for routing and local state for draft idempotency. `draft-reports --dry-run`
  validates recipients and finished attachment names without touching Gmail;
  `draft-reports --dry-run --prepare-files` also writes/repairs the local
  report folders while still avoiding Gmail.
- `python -m tracking.cli contacts-init` creates a local starter `contacts.csv`
  from processed client folders when the real file is missing. Starter rows are
  disabled and have blank PC emails, and the command refuses to overwrite an
  existing contact file unless `--add-missing` is used to append only missing
  processed clients as disabled starter rows.
- Run state tracks last run, pending JobIDs, processed sends, and draft IDs.
  `python -m tracking.cli status` shows pending reasons, message counts,
  folder keys, processed folders, draft IDs, and draft-readiness blockers such
  as missing or invalid contact routing.
- Windows Task Scheduler can be installed with
  `.\scripts\install_scheduled_task.ps1`; it calls the hidden VBS wrapper, which
  calls `python -m tracking.cli run` and preserves the runner exit code for
  Task Scheduler or Power Automate failure notifications.
- Scheduler logs rotate through `logs/run.log.1` when `logs/run.log` exceeds the
  configured byte cap.
- Scheduled runs refresh `logs/status.json` from `python -m tracking.cli status
  --json`, giving Power Automate or other wrappers structured pending and draft
  blocker data without scraping the text log.
- Direct SFMC/ExactTarget export is now automated end-to-end by
  `scripts/pull_reports.py` (`init` / `build` / `status`), superseding the
  older `sfmc-stage`-into-`drop/processed` path. `init` scaffolds a run
  manifest at `runs/<run-id>/manifest.json`; `build` pulls each send's
  engagement CSVs, reconstructs the overview PDF from the `Send` object and
  tracking events (no manual overview-PDF email required for this path),
  produces the Lead Scoring export, writes the Print Status Report row and
  calendar mark fill-blanks-only, and creates Gmail drafts only; `status`
  reads the manifest as the authoritative record of what happened per send.
  `build --dry-run` computes the same plan without writing anything. A send
  with zero Unique Opens/Clicks is parked as `needs_confirmation` until
  released with `--confirm-zero <send_id>`, and HIPAA-flagged sends skip the
  Kathryn notification. `sfmc-probe`/`sfmc-stage` still exist for the older
  Gmail-intake-completion path but are no longer the recommended route for
  pulling sends directly from SFMC.
- Scheduled `run --drafts` creates drafts only after pull and Sheet write-back
  complete cleanly in the same run.

## Not Automated Yet

- Sending report emails is not automated. Drafts are created for review only.
- Lead scoring is intentionally deferred. Lead-score files remain identified and
  ignored by the engagement pipeline.
- Official overview PDFs remain required for the Gmail-intake path. Sends
  without an overview PDF email stay pending instead of being guessed from
  partial data. (The direct `scripts/pull_reports.py` path is unaffected —
  it reconstructs the overview PDF itself from the `Send` object and
  tracking events, so it does not wait on that email.)
- Draft creation is blocked until the git-ignored `contacts.csv` has reviewed
  PC emails and enabled delivery rows. `contacts.example.csv` is committed as
  the operator template, and `contacts-init` can generate a local starter from
  processed sends or append missing processed clients with `--add-missing`.
- Power Automate Desktop is not part of core business logic. It should only wrap
  the Python CLI for notifications or operator convenience.
- CodeRabbit did not leave actionable PR feedback on the merged automation PR.

## Current Live State

`python -m tracking.cli status` currently reports:

- 4 pending JobIDs, each blocked on `awaiting overview-PDF email (identity) for
  this JobID`.
- 5 processed send folders present.
- 0 drafted reports.
- Draft readiness blocked because the generated `contacts.csv` starter rows are
  still disabled pending recipient review.

The pending JobIDs have export-message breadcrumbs in status output, but no
local staged files remain in `drop/inbox`; the blocker is the missing overview
PDF email/artifact, not a local file move.

`contacts.csv` now exists locally as a git-ignored starter generated from the
processed sends. Gmail draft creation cannot be accepted live yet because each
row is disabled and has no reviewed PC email. Fill those starter rows, then
confirm `python -m tracking.cli status` reports draft readiness before running
`python -m tracking.cli draft-reports` or `run --drafts`.

## Power Automate Search Evidence

A targeted local search checked these locations:

- `%LOCALAPPDATA%\Microsoft\Power Automate Desktop`
- `%APPDATA%\Microsoft\Power Automate Desktop`
- `%USERPROFILE%\Documents`
- `%USERPROFILE%\OneDrive\Documents`
- `%USERPROFILE%\Desktop\TRACKINGREPORTS`

Results found Power Automate Desktop settings/bookmark JSON files and the
tracking procedure/report artifacts, but no readable/exported tracking flow
package to depend on. The supported route is therefore Python-first with Task
Scheduler or Power Automate as an optional wrapper.

## Review Gate

PR #1, `Automate engagement completion`, has been merged into `main`:
<https://github.com/weaverjsdw-ux/TrackingRepo/pull/1>

Follow-up hardening is consolidated on branch
`automation-followups-consolidated`:
<https://github.com/weaverjsdw-ux/TrackingRepo/pull/new/automation-followups-consolidated>

Review evidence:

- PR comments: none.
- PR reviews: none.
- PR review threads: none.
- CodeRabbit did not leave actionable feedback.
- No workflow runs were visible through the GitHub connector for the PR head SHA.

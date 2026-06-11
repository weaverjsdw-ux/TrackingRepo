# Tracking Reports Automation Status

Last reviewed: 2026-06-11

## Automated Now

- Gmail intake groups ExactTarget export emails by JobID, stages attachments,
  deduplicates by content hash, processes complete sends, removes the intake
  label, and moves completed sends to `drop/processed`.
- Parser/pipeline identifies report files by content, ignores lead scoring,
  counts metrics, resolves BH, parses the overview PDF, and creates the Sheet
  write plan.
- Sheet write-back updates the configured Google Sheet with fill-blanks-only
  safety by default.
- Filing creates the renamed report folder for each processed send.
- Gmail report delivery creates drafts only, never sends, using `contacts.csv`
  for routing and local state for draft idempotency.
- Run state tracks last run, pending JobIDs, processed sends, and draft IDs.
  `python -m tracking.cli status` shows pending reasons, message counts,
  folder keys, processed folders, and draft IDs.
- Windows Task Scheduler can be installed with
  `.\scripts\install_scheduled_task.ps1`; it calls the hidden VBS wrapper, which
  calls `python -m tracking.cli run`.
- Scheduler logs rotate through `logs/run.log.1` when `logs/run.log` exceeds the
  configured byte cap.
- SFMC/ExactTarget API automation is gated behind `sfmc-probe`; `sfmc-stage`
  only runs after explicit URL templates are configured and the probe passes.

## Not Automated Yet

- Sending report emails is not automated. Drafts are created for review only.
- Lead scoring is intentionally deferred. Lead-score files remain identified and
  ignored by the engagement pipeline.
- Official overview PDFs remain required. Sends without an overview PDF stay
  pending instead of being guessed from partial data.
- Power Automate Desktop is not part of core business logic. It should only wrap
  the Python CLI for notifications or operator convenience.
- CodeRabbit review cannot run until a GitHub PR exists.

## Current Live State

`python -m tracking.cli status` currently reports:

- 4 pending JobIDs, each blocked on `awaiting overview-PDF email (identity) for
  this JobID`.
- 5 processed send folders present.
- 0 drafted reports.

The pending JobIDs have export-message breadcrumbs in status output, but no
local staged files remain in `drop/inbox`; the blocker is the missing overview
PDF email/artifact, not a local file move.

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

Branch `automate-engagement-completion` is pushed. PR creation is currently
blocked by GitHub tooling permissions:

- GitHub connector PR creation returns `403 Resource not accessible by
  integration`.
- GitHub CLI (`gh`) is not installed.
- No `GH_TOKEN`, `GITHUB_TOKEN`, or `GITHUB_PAT` environment variable is present.
- `git credential fill` did not expose a reusable GitHub credential.

Create the PR from:
<https://github.com/weaverjsdw-ux/TrackingRepo/pull/new/automate-engagement-completion>

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
  for routing and local state for draft idempotency. `draft-reports --dry-run`
  validates recipients and finished attachment names without touching Gmail.
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
- SFMC/ExactTarget API automation is gated behind `sfmc-probe`; `sfmc-stage`
  only runs after explicit URL templates are configured and the probe passes.
  Complete SFMC artifact sets are validated through the normal parser and
  promoted into `drop/processed` so existing Sheet, draft, and status commands
  consume them through the same path as Gmail intake.
- Scheduled `run --drafts` creates drafts only after pull and Sheet write-back
  complete cleanly in the same run.

## Not Automated Yet

- Sending report emails is not automated. Drafts are created for review only.
- Lead scoring is intentionally deferred. Lead-score files remain identified and
  ignored by the engagement pipeline.
- Official overview PDFs remain required. Sends without an overview PDF stay
  pending instead of being guessed from partial data.
- Draft creation is blocked until a real, git-ignored `contacts.csv` exists.
  `contacts.example.csv` is committed as the operator template.
- Power Automate Desktop is not part of core business logic. It should only wrap
  the Python CLI for notifications or operator convenience.
- CodeRabbit did not leave actionable PR feedback on the merged automation PR.

## Current Live State

`python -m tracking.cli status` currently reports:

- 4 pending JobIDs, each blocked on `awaiting overview-PDF email (identity) for
  this JobID`.
- 5 processed send folders present.
- 0 drafted reports.
- Draft readiness blocked because `contacts.csv` is missing.

The pending JobIDs have export-message breadcrumbs in status output, but no
local staged files remain in `drop/inbox`; the blocker is the missing overview
PDF email/artifact, not a local file move.

`contacts.csv` is not present in the checkout, so Gmail draft creation cannot be
accepted live yet. Add the real client routing file from `contacts.example.csv`,
then confirm `python -m tracking.cli status` reports draft readiness before
running `python -m tracking.cli draft-reports` or `run --drafts`.

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

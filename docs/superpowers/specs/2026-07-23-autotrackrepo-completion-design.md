# AutoTrackRepo — Completion Package Design

**Date:** 2026-07-23
**Goal:** Finalize the manifest-driven SFMC report-pull tool as a done,
production-ready program named **AutoTrackRepo**, landed on `main`, with a
single durable "build complete" marker.

**Nature:** finishing + documentation only. **No behavior or code change to the
tool.** "AutoTrackRepo" is the program/release name; the entry point stays
`scripts/pull_reports.py` (renaming the file would break the operator's habits
and the scheduled-task wiring for no benefit — YAGNI).

---

## Why now

The substance is already done: spec approved, plan executed (13 TDD tasks with
task-level + whole-branch review), **159 automated tests passing**, and the tool
**live-validated end-to-end** on a real send. Three real bugs/changes surfaced by
that live run are committed. What's missing is the *finishing*: the work sits on
the `automation-followups-consolidated` branch with no clean, durable marker that
it's complete.

## Deliverables

### 1. `docs/AutoTrackRepo-DONE.md` (new) — the completion artifact

The single dated, human-readable **definition of done**: a checklist, every item
checked, each pointing at its proof.

- ✅ **Spec approved & committed** → `docs/superpowers/specs/2026-07-15-sfmc-report-pull-design.md`
- ✅ **Plan executed** → `docs/superpowers/plans/2026-07-15-sfmc-report-pull.md` (13 tasks, TDD, per-task + whole-branch review)
- ✅ **Automated tests: 159 passing** → reproduce: `./.venv/Scripts/python.exe -m pytest -q`
- ✅ **Live end-to-end validation (2026-07-23)** on *Woodside Priory School Spring 2026 eNL* (SFMC Send **691944**):
  - 8 engagement CSVs + styled PDF + Lead Scoring CSV written to disk (row counts tie out: 670 / 210 / 28 / 1 / 77 / 12 / 66 / 6)
  - Print Status **row 525** — 9 cells filled and read back correct
  - Calendar **July 2026 J44** marked `7/23 JS`
  - Report draft + Kathryn draft created and verified in Gmail
- ✅ **3 post-validation fixes:** calendar int-cell crash (`4aa3391`), Kathryn attach-the-file (`73a4bfb`), Kathryn empty-body (`e44d803`)
- ✅ **Landed on `main` and tagged `AutoTrackRepo`**

Also records: the run recipe, the manifest location (`runs/<YYYY-MM>/manifest.json`,
gitignored), and the send-ID selection gotcha (one send *name* → many SFMC IDs;
pick the production blast by largest `NumberSent`).

### 2. `docs/AUTOMATION_STATUS.md` (update)

- "Last reviewed" → **2026-07-23**.
- In the SFMC / `pull_reports.py` section: mark **AutoTrackRepo live-validated &
  production-ready**, cite the Woodside proof, and add the run recipe + the
  dry-run-first note.

### 3. `README.md` (update)

- A short **"AutoTrackRepo — Status: ✅ Complete"** block: one line on what it
  does, the 3-step recipe (`init` → fill the run manifest → `build` / `status`),
  the dry-run-first habit, and a pointer to `docs/AutoTrackRepo-DONE.md`.

### 4. Land on `main`

- `main` is **0 commits behind** HEAD, so this is a **fast-forward**: `main`
  advances to the branch HEAD. No conflicts, no merge commit.
- Brings **all branch commits** — the ~22 AutoTrackRepo commits (spec → build →
  live fixes) plus the ~38 prior consolidated-followups commits — all committed
  and green at HEAD. *(Operator approved landing the whole branch.)*
- The **uncommitted working-tree files** (`src/tracking/filing.py`,
  `tests/test_cli.py`, `tests/test_filing.py`, `drop/.intake_state.json`,
  untracked `docs/SFMC_INTEGRATION_PLAN.md`) are in no commit → they stay in the
  working tree, untouched, and do **not** go to `main`.

### 5. Tag

- Annotated tag **`AutoTrackRepo`** on `main`'s new HEAD.
- Message = completion summary (what it does, live-validated Woodside 691944 on
  2026-07-23, 159 tests green, the 3 fixes). Retrievable via
  `git show AutoTrackRepo`.

## Sequencing

1. Write deliverables 1–3 on the branch; commit them.
2. Re-run the suite; confirm **159 passed**.
3. Fast-forward `main` to the branch HEAD.
4. Create annotated tag `AutoTrackRepo` on `main`.
5. Leave the pre-existing unrelated dirty files as-is.

## Out of scope

- Any behavior/code change to the tool; renaming `scripts/pull_reports.py`.
- Pushing to a remote / opening a PR (local only unless asked).
- The Subject-line `.strip()` tidy-up (separate optional follow-up).
- Sending the drafts (operator step).

## Verification

- `./.venv/Scripts/python.exe -m pytest -q` → `159 passed`.
- `git tag -l` lists `AutoTrackRepo`; `git show AutoTrackRepo` prints the summary.
- `main` HEAD == branch HEAD (the doc commit).
- `git status` still shows only the pre-existing dirty files (nothing swept in).

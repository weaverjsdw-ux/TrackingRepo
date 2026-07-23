# Coworker Handoff Package — Design

**Date:** 2026-07-23
**Status:** Draft — awaiting operator review.
**Goal:** Produce everything a second operator needs to run the Tracking Reports
tooling (both report paths) on their own Windows machine — without guessing at any
of the account/credential/path coupling that `git clone` does not carry.

**Nature:** **Documentation + secrets-provisioning only. No tool behavior or code
change.** The tool is already env-var driven; for a same-org second operator, even
the calendar initials are an env var, so nothing in the code needs to change.

---

## 1. Locked decisions (from operator Q&A, 2026-07-23)

| Decision | Choice |
|---|---|
| Which workflows the coworker runs | **Both**: the direct-SFMC `pull_reports.py` path **and** the Gmail-intake `tracking.cli` path |
| Scheduler | **Optional** — documented as an appendix; manual runs are the default |
| Handoff type | **Second operator / ongoing backup** (both people can run it) |
| Google identity | **Shared service account** (reused for Sheets + calendar) **+ the coworker's own Gmail** for authorization |
| SFMC credentials | **Reuse the existing Installed Package** (share the client id/secret securely) |
| Org hardcodes (`kathryn.baugh@pentera.com`, intake sender addresses) | **Leave as-is** — same org, so they already work; **no code edit** |
| OS | **Windows** |

## 2. The handoff reality (why this is mostly docs, not code)

`git clone` gives the coworker the code plus three `*.example` templates and
**nothing that makes it run**. Every secret and runtime path is git-ignored and
exists only on the current machine. The coworker must recreate, on their box:

- a fresh `.venv` (the on-disk venv is bound to Python 3.14.5 on this machine) and
  `pip install -e .[gmail,sheets,dev]`;
- a new `.env` with **their** `REPORTS_DIR`, **their** `CALENDAR_MARK_INITIALS`,
  and the shared `SHEET_ID` / `SHEET_TAB` / `CALENDAR_ID` / `SFMC_*` values;
- `secrets/service-account.json` and `secrets/client_secret.json` (both **shared**
  from the primary operator — a service-account key and an OAuth *app* client are
  not per-user), then a one-time `authorize` signed into **their own Gmail** to
  mint **their own** `secrets/token.json`;
- the `tracking-reports` Gmail label in their inbox; a filled `contacts.csv`.

**Documentation gap this package fills:** there is no onboarding doc for the live
`pull_reports.py` path. [OPERATOR_SETUP.md](../../OPERATOR_SETUP.md) predates it and,
worse, describes standing up a *separate* Google project + service account — the
opposite of the "reuse the shared service account" decision. And
`docs/AutoTrackRepo-DONE.md` is referenced but was never written.

**Security note:** the current `.env` / `secrets/` hold live plaintext credentials
(SFMC client secret, Google OAuth client secret, the service-account private key,
the Gmail token). Sharing the service-account file means sharing a private key —
acceptable for a trusted colleague, but it shapes the transfer step (§4.5) and
argues for rotating those secrets if the coworker later leaves.

## 3. One thing to confirm — the Gmail intake inbox (open item)

The tool uses **one** Gmail authorization for **both** reading inbound report
emails (intake) **and** creating drafts. So "the coworker's own Gmail" governs
*both*: intake would read **their** mailbox, and drafts land in **their** mailbox.

That is exactly right for the **direct-SFMC path** (it never reads inbound mail —
it only creates drafts). But the **Gmail-intake path** reads whichever inbox the
ExactTarget export emails actually arrive in. If those arrive at the primary
operator's / a shared report inbox, the coworker's own-Gmail token will not see
them. Options, in recommended order:

- **(Recommended) The coworker runs the direct-SFMC path with own-Gmail drafts;
  the intake path stays with the primary operator** until a shared-inbox decision
  is made. The direct-SFMC `pull_reports.py` path is the newly-completed tool and
  the bulk of the value, and it fits "own Gmail" cleanly.
- **Shared report inbox for intake:** the coworker authorizes the *shared* report
  inbox instead of their own (drafts then also land there). Simplest for intake,
  but drafts are no longer in "their own" inbox.
- **Deliver a copy to the coworker:** a forward/filter/delegation so the export
  emails also reach the coworker's inbox, letting their own token run intake.

The onboarding doc will document the recommended split and note the two variants.
**Operator to confirm which they want** before/at spec review.

## 4. Deliverables

### 4.1 `docs/COWORKER_ONBOARDING.md` (new — the main artifact)

A single, self-contained, second-operator runbook for Windows. Sections:

1. **Who this is for / prerequisites** — Windows, Python ≥3.11, access to the
   private GitHub repo, a Google account for their own Gmail drafts.
2. **What the primary operator sends you out-of-band** — the transfer checklist
   (§4.5): the two `secrets/*.json` files, the SFMC creds, and the shared sheet
   IDs. Plus the buddy-side actions (add the coworker as a Google **test user**;
   confirm the service account is Editor on both sheets; grant repo access).
3. **Get the code** — clone the private repo.
4. **Build the environment** — `python -m venv .venv`; activate;
   `pip install -e .[gmail,sheets,dev]`; prove it with `pytest -m "not realdata"`.
5. **Drop in the shared secrets** — save `service-account.json` and
   `client_secret.json` into `secrets/`.
6. **Write your `.env`** — copy `.env.example`; a **SHARED vs. YOURS** table for
   every variable (paste the shared block; set `REPORTS_DIR` and
   `CALENDAR_MARK_INITIALS` to yours).
7. **Authorize your Gmail** — `python -m tracking.cli authorize`, sign into **your**
   inbox → mints your `secrets/token.json`. Create the `tracking-reports` label.
8. **Path 1 — Direct SFMC pull (AutoTrackRepo):** `init` → fill the manifest
   (client/season/year/type, `send_id` picked by **largest `NumberSent`**,
   `booklet_selector`, `lead_scoring_de`, the **required** `hipaa` flag) →
   `build --dry-run` → `build` → `status`; releasing a `needs_confirmation` send
   with `--confirm-zero`; **review and send drafts by hand**.
9. **Path 2 — Gmail intake (emailed sends):** `pull` → `status` →
   `write --commit` → `draft-reports --dry-run` → `draft-reports`; `contacts.csv`
   must be filled and enabled first. (Gated by the §3 inbox decision.)
10. **Appendix — Optional unattended scheduler** — `install_scheduled_task.ps1`,
    per-machine, only after manual runs work; explicitly optional.
11. **Two operators — coordination** (§4.6).
12. **Troubleshooting** — missing env → `SfmcConfigError`; token expired
    (~7-day Testing-mode expiry) → re-`authorize`; ambiguous sheet row; MAX_PATH.
13. **Security & secrets hygiene** — never commit `.env`/`secrets/` (gitignore
    already covers them); protect the shared service-account key; rotate on exit.

### 4.2 `.env.example` (edit)

Reframe the file from the old probe/stage era to the **live** `pull_reports.py` +
intake workflow: promote the currently-commented live block
(`SFMC_AUTH/REST/SOAP_BASE_URL`, `SFMC_CLIENT_ID/SECRET`, `SHEET_ID`, `SHEET_TAB`,
`GOOGLE_SHEETS_SERVICE_ACCOUNT`, `CALENDAR_ID`, `CALENDAR_MARK_INITIALS`,
`LEAD_SCORING_DIR`) into an active, clearly-annotated section, with each line
marked **SHARED** (paste from the primary operator) or **YOURS** (`REPORTS_DIR`,
`CALENDAR_MARK_INITIALS`). Secret values stay blank placeholders; no real
`SFMC_CLIENT_SECRET` in the committed example.

### 4.3 `README.md` (edit)

Add a short pointer to `docs/COWORKER_ONBOARDING.md` and a one-line
"second operator / backup" note near the run instructions.

### 4.4 `docs/AutoTrackRepo-DONE.md` (new — small bonus, optional)

Write the referenced-but-missing completion marker so the README / onboarding
links resolve. Optional; can be dropped from this package if the operator prefers
to keep scope to the handoff.

### 4.5 Secrets & access transfer checklist (content, not a committed secret)

Lives as a section inside the onboarding doc (it names *which files* to send, never
their contents). Split:

- **Primary operator sends securely (out of band — not email, not git):**
  `secrets/service-account.json`, `secrets/client_secret.json`, the five `SFMC_*`
  values, and `SHEET_ID` / `SHEET_TAB` / `CALENDAR_ID`.
- **Primary operator does in the consoles:** add the coworker's Google account as a
  **Test user** on the OAuth consent screen (else `authorize` is blocked); confirm
  the service account is Editor on the Print Status sheet *and* the calendar sheet;
  grant the coworker access to the private repo.
- **Coworker creates themselves:** the venv, their `.env` (own `REPORTS_DIR` +
  initials), their `token.json` (via `authorize`), the Gmail label, `contacts.csv`.

### 4.6 Two-operator coordination note (section in the onboarding doc)

- Sheet and calendar writes are **fill-blank-only**, so a value one operator wrote
  is never overwritten by the other — the shared spreadsheets are safe.
- The real risk is **both operators drafting/sending the same send** (each has an
  independent manifest + inbox, so draft-idempotency does not span operators).
- Rule: agree who owns which sends per cycle (e.g., primary owns the cycle; backup
  covers named sends or when primary is out). Keep it lightweight.

## 5. Out of scope

- Any tool behavior/code change (including moving the org hardcodes to config —
  explicitly declined for this same-org coworker).
- Minting a separate SFMC package or a separate service account (reuse chosen).
- Google Drive (the tool files locally, not to Drive).
- Mac/Linux porting (Windows chosen).
- Sending the drafts, and the HIPAA→PC delivery branch (operator/other workflows).
- Rewriting `OPERATOR_SETUP.md` (left as the first-operator/from-scratch doc; the
  onboarding doc references its Google-console mechanics rather than duplicating).

## 6. Sequencing

1. Write `docs/COWORKER_ONBOARDING.md` (§4.1).
2. Edit `.env.example` (§4.2) and `README.md` (§4.3).
3. (Optional) write `docs/AutoTrackRepo-DONE.md` (§4.4).
4. Verify (§7); commit the docs together on the working branch.

## 7. Verification

- **Doc accuracy:** every command, env var, path, and file reference in the
  onboarding doc matches the code (`pull_reports.py`, `cli.py`, `sfmc.py`,
  `sheets_writer.py`, `gmail_source.py`) and `.env.example` — no invented flags.
- **Install proof:** the documented setup ends in `pytest -m "not realdata"`
  passing, so a coworker following it can confirm their environment before any
  live run.
- **No secrets committed:** `git status` shows only the doc/example changes; no
  `.env`, `secrets/*`, `contacts.csv`, or `runs/` content is staged.
- **Links resolve:** README → onboarding doc → (optional) DONE doc all exist.

## 8. Approval scope

This spec covers only the handoff documentation package above. It does **not**
touch the pre-existing dirty working-tree files, does not send anything, and makes
no code change. The §3 Gmail-intake-inbox choice is the one open item to confirm.

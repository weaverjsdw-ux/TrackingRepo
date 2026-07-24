# Coworker Onboarding — running Tracking Reports as a second operator

This gets a **second operator** running the engagement Tracking Reports tooling on
their **own Windows machine**, reusing the team's shared Google service account and
SFMC package. You'll do the reports two ways:

- **Path 1 — Direct SFMC pull (`pull_reports.py`)** — for sends pulled straight from
  Marketing Cloud. This is the primary path for you, and drafts land in **your** Gmail.
- **Path 2 — Gmail intake (`tracking.cli`)** — for sends that arrive as ExactTarget
  export emails. By default this stays with the primary operator; see
  [Path 2](#path-2--gmail-intake-emailed-sends) if you need to run it yourself.

> **Why you can't just `git clone` and go.** The repo carries the *code* only.
> Every secret, credential, and machine path is git-ignored and lives only on the
> operator's box. You recreate those below. It's about 30 minutes, mostly one-time.

---

## 0. Prerequisites

- **Windows** with **PowerShell**.
- **Python ≥ 3.11** installed and on `PATH` (`python --version`).
- Access to the **private GitHub repo** (the primary operator grants this).
- A **Google account** you'll use for your own Gmail drafts (your work Gmail is fine).

---

## 1. What the primary operator sends you (out of band)

These are **shared** and must come to you **securely** — not over plain email, not in
git. Use a password manager share, an encrypted file, or hand-off in person.

**Files** (you'll save these into `engagement-tracker\secrets\`):
- `service-account.json` — the shared Google service-account key (writes the Sheet + calendar).
- `client_secret.json` — the shared Google OAuth *app* client (lets you authorize your Gmail).

**Values** (you'll paste these into your `.env` in step 5):
- `SFMC_AUTH_BASE_URL`, `SFMC_REST_BASE_URL`, `SFMC_SOAP_BASE_URL`
- `SFMC_CLIENT_ID`, `SFMC_CLIENT_SECRET`
- `SHEET_ID`, `SHEET_TAB`, `CALENDAR_ID`

**And the primary operator does these for you** (you can't do them yourself):
- **Adds your Google account as a Test user** on the OAuth consent screen — without
  this, step 7's `authorize` is blocked. (Console path: APIs & Services → OAuth
  consent screen → Test users → Add. See [OPERATOR_SETUP.md §3](OPERATOR_SETUP.md).)
- Confirms the service account is **Editor** on both the Print Status sheet **and**
  the calendar sheet (it already is for the current sheets).
- Grants you access to the **private repo**.

---

## 2. Get the code

```powershell
git clone <private-repo-url> TRACKINGREPORTS
cd TRACKINGREPORTS\engagement-tracker
```

Everything below runs from the `engagement-tracker` folder.

---

## 3. Build the Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[gmail,sheets,dev]"
```

The `gmail` and `sheets` extras are **required** for live runs (the Google libraries
are optional otherwise). `dev` gives you the test suite.

**Prove the install** with the offline, PII-free test suite — it needs no credentials:

```powershell
python tests\fixtures\_generate_synthetic.py   # (re)build synthetic fixtures
python -m pytest -q -m "not realdata"
```

Green here means your environment is sound before you touch any live service.

---

## 4. Drop in the shared secret files

Save the two files from step 1 into the `secrets\` folder (already git-ignored):

```
engagement-tracker\secrets\service-account.json
engagement-tracker\secrets\client_secret.json
```

Do **not** commit these or paste their contents anywhere. You do **not** get the
primary operator's `token.json` — you'll mint your own in step 7.

---

## 5. Write your `.env`

Copy the template and open it:

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill it in using this table. **SHARED** = paste the value the primary operator sent
you. **YOURS** = specific to you and your machine.

| Variable | Who | Value |
|---|---|---|
| `REPORTS_DIR` | **YOURS** | Absolute path to your output folder, e.g. `C:\Users\<you>\Desktop\TRACKINGREPORTS\Completed Reports` |
| `CALENDAR_MARK_INITIALS` | **YOURS** | Your initials (they appear on the calendar, e.g. `7/24 AB`) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | shared file | `secrets/client_secret.json` (the file from step 4) |
| `GOOGLE_TOKEN_PATH` | **YOURS** | `secrets/token.json` (minted in step 7 — starts absent) |
| `GOOGLE_SHEETS_SERVICE_ACCOUNT` | shared file | `secrets/service-account.json` |
| `SHEET_ID` | **SHARED** | the Print Status Report spreadsheet ID |
| `SHEET_TAB` | **SHARED** | `2026 Print Status Report` |
| `CALENDAR_ID` | **SHARED** | the calendar spreadsheet ID |
| `SFMC_AUTH_BASE_URL` | **SHARED** | `https://<subdomain>.auth.marketingcloudapis.com/` |
| `SFMC_REST_BASE_URL` | **SHARED** | `https://<subdomain>.rest.marketingcloudapis.com/` |
| `SFMC_SOAP_BASE_URL` | **SHARED** | `https://<subdomain>.soap.marketingcloudapis.com/` |
| `SFMC_CLIENT_ID` | **SHARED** | the Installed Package client id |
| `SFMC_CLIENT_SECRET` | **SHARED** | the Installed Package client secret |
| `LEAD_SCORING_DIR` | optional | defaults to `<REPORTS_DIR>\Lead Scoring` — leave unset unless you want it elsewhere |
| `GMAIL_LABEL` | default | `tracking-reports` (only matters for Path 2) |
| `CONTACTS_CSV` | default | `contacts.csv` (only matters for Path 2) |

---

## 6. (One-time) create your `REPORTS_DIR`

```powershell
New-Item -ItemType Directory -Force "$env:REPORTS_DIR"   # or just create the folder you named above
```

The tool writes report folders and (by default) a `Lead Scoring\` subfolder here.

---

## 7. Authorize your Gmail

This opens a browser, you sign into **your** inbox, and it writes your own
`secrets/token.json`:

```powershell
python -m tracking.cli authorize
```

If the browser says the app is unverified / you're not a test user, the primary
operator hasn't added your Google account as a Test user yet (step 1) — ask them to.

Then, in your Gmail, **create a label named `tracking-reports`** (only needed if you
run Path 2, but harmless to make now).

> **Heads-up — the token expires.** The OAuth app is in "Testing" mode, so your
> `token.json` stops working roughly every 7 days. When drafts suddenly fail auth,
> just re-run `python -m tracking.cli authorize`. This is normal.

---

## Path 1 — Direct SFMC pull (`pull_reports.py`)

This is your main workflow. It pulls a send straight from Marketing Cloud and
produces the CSVs, the PDF, the Lead Scoring export, the Print Status row, the
calendar mark, and the Gmail drafts — all recorded in one manifest.

### Step A — scaffold this run's manifest

```powershell
python scripts\pull_reports.py init
```

This creates `runs\<YYYY-MM>\manifest.json` (defaults to the current month) with a
template send entry. Nothing is hardcoded — you fill in the facts.

### Step B — fill in the sends

Open `runs\<YYYY-MM>\manifest.json` and, for each send, set:

| Field | Notes |
|---|---|
| `client`, `season`, `year`, `type` | Identity, e.g. `Yale New Haven Hospital` / `Spring` / `2026` / `eNL` |
| `send_id` | The SFMC SendID. **If several sends share one name, pick the production blast — the one with the largest `NumberSent`.** `build --dry-run` prints the resolved `NumberSent` so you can confirm you chose right. |
| `booklet_selector` | `init` prefills this by type; confirm it matches the booklet link for this send |
| `lead_scoring_de` | Optional. If omitted it's derived as `sd_<Client> - Lead Scoring`. **Set it explicitly when the data-extension name differs from the client name** (some do). |
| `hipaa` | **Required — no default.** `true` or `false`. If absent, that send fails loud. HIPAA sends export the data file but **skip** the Kathryn lead-score notification. |

### Step C — dry run (reads APIs, writes nothing)

```powershell
python scripts\pull_reports.py build --dry-run
```

Review the planned CSV set, the sheet row + cells, the calendar tab + cell, and the
draft recipients/attachments. Nothing is written — no files, drafts, sheet, or
calendar changes.

### Step D — build for real

```powershell
python scripts\pull_reports.py build
```

Writes the CSVs + PDF + Lead Scoring file, fills the Print Status row and calendar
cell (**blanks only** — it never overwrites an existing value), and creates the
Gmail drafts (report draft + Kathryn draft) in **your** inbox.

- A send with **zero Unique Opens/Clicks** is parked as `needs_confirmation` and its
  sheet/calendar/draft side-effects are skipped until you release it:
  ```powershell
  python scripts\pull_reports.py build --confirm-zero <send_id>
  ```
- Useful flags: `--only <send_id>` (one send), `--skip-drafts`, `--skip-calendar`,
  `--force` (write non-blank cells too — use with care).

### Step E — see what happened

```powershell
python scripts\pull_reports.py status
```

This reads the manifest and is the authoritative record of the run.

### Step F — send the drafts (manual)

The tool **only ever creates drafts, never sends.** Open Gmail, review each report
draft, **add the recipients** (the report draft is created with blank To on purpose),
and send. The Kathryn lead-score draft already has its recipient and the file
attached — review and send.

---

## Path 2 — Gmail intake (emailed sends)

**By default this path stays with the primary operator.** The tool uses a *single*
Gmail login for both reading inbound export emails and creating drafts, so your
own-Gmail token reads *your* inbox — where the ExactTarget export emails don't
arrive. To run intake yourself, pick one:

- **Variant 2 — authorize the shared report inbox.** Run `authorize` and sign into
  the **shared report inbox** (the one the export emails land in) instead of your
  own. Note: your drafts will then land in that shared inbox, not yours.
- **Variant 3 — get a copy delivered to you.** Have the primary operator set up a
  forward/filter/delegation so the export emails also reach your inbox; then your
  own-Gmail token can run intake.

Once your token points at an inbox that receives the export emails, and you've
created the `tracking-reports` label and filled `contacts.csv` (below), the workflow is:

```powershell
python -m tracking.cli pull                    # pull + stage labeled report emails
python -m tracking.cli status                  # what's pending / processed / blockers
python -m tracking.cli write --commit          # write counts to the Sheet
python -m tracking.cli draft-reports --dry-run # validate recipients + attachment names
python -m tracking.cli draft-reports           # create the report drafts (never sends)
```

**`contacts.csv` (Path 2 only).** Report drafts here are routed from a local
`contacts.csv`. Create a starter after at least one send is processed:

```powershell
python -m tracking.cli contacts-init           # writes a disabled starter contacts.csv
```

Fill each row's `pc_email` and set `report_delivery_enabled=yes` **after** you've
reviewed the recipient. Confirm `python -m tracking.cli status` reports draft
readiness before `draft-reports`. (Path 1 does **not** use `contacts.csv` — its
report draft is created with blank recipients that you fill by hand.)

---

## Appendix — Optional unattended scheduler (Windows)

You do **not** need this to do the reports — it's for hands-off, repeated runs of the
intake path. Most operators run the commands manually. If you do want it:

```powershell
.\scripts\install_scheduled_task.ps1                 # every 4 minutes, hidden window
.\scripts\install_scheduled_task.ps1 -IntervalMinutes 30
.\scripts\install_scheduled_task.ps1 -Drafts         # only after contacts.csv is reviewed
```

It registers a Task Scheduler job under **your** Windows account, so it must be
installed on your machine and only after the manual commands work. Leave `-Drafts`
off until contact routing is reviewed.

---

## Two operators — coordination

You and the primary operator can both run the tooling. Two things to know:

- **The shared Sheet and calendar are safe.** Both writes are **fill-blank-only**, so
  a value one of you already wrote is never overwritten by the other — the second
  run just skips and flags it.
- **The real risk is drafting/sending the same send twice.** Draft de-duplication is
  per-operator (each of you has your own manifest and your own inbox), so if you both
  process the same send you'll each create — and could each send — a draft.

**Rule of thumb:** agree who owns each cycle. Simplest split — the primary operator
owns the cycle by default; you cover specific named sends, or the whole cycle when
they're out. Keep it lightweight; the fill-blank safety catches honest overlaps.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `SfmcConfigError` / missing-credential failure | A required `SFMC_*` (or Sheet/Calendar) var is unset in `.env`. Re-check step 5. |
| Draft creation fails with an auth error | Your `token.json` expired (~7-day Testing-mode limit). Re-run `python -m tracking.cli authorize`. |
| `authorize` browser says you're not a test user | The primary operator hasn't added your Google account as a Test user yet (step 1). |
| A send is stuck as `needs_confirmation` | Zero Unique Opens/Clicks. Verify it's real, then `build --confirm-zero <send_id>`. |
| Sheet write "fails loud" on an ambiguous row | Two candidate rows matched Client+Type. The output lists the candidates — resolve the Sheet by hand, or check the send's season/type. |
| A file "silently" didn't attach | Windows MAX_PATH (260) with a long client name. The tool uses absolute paths to avoid this; if you moved `REPORTS_DIR` very deep, shorten it. |

---

## Security & secrets hygiene

- **Never commit `.env`, `secrets\*`, `contacts.csv`, or anything under `runs\`.** The
  `.gitignore` already blocks these — don't override it.
- The shared `service-account.json` is a **private key**. Store it like a password;
  don't forward it onward.
- If you stop operating the reports, tell the primary operator so they can rotate the
  shared secrets and remove your Test-user access.

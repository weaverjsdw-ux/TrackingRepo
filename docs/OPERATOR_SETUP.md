# Operator setup — Google credentials, label, and sheet

One-time setup to take the tool live. Produces three things the tool needs:

1. `secrets/client_secret.json` — OAuth client for **Gmail** (reads labeled report emails and creates drafts).
2. `secrets/service-account.json` — service account for **Google Sheets** (writes the counts).
3. The service account's **email address** (`…iam.gserviceaccount.com`) — used to share the sheet.

> **Security:** put both JSON files in `engagement-tracker/secrets/`. That folder is
> already git-ignored (`credentials*.json`, `service-account*.json`,
> `client_secret*.json`) so they will **never** be committed. Do not paste their
> contents anywhere. The service-account *email* is fine to share; the key file is not.

UI labels in the Google Cloud Console shift over time; the path names below are
the stable landmarks — look for the nearest equivalent if wording differs.

---

## 1. Create a Google Cloud project
1. Go to <https://console.cloud.google.com>.
2. Top bar → **project dropdown** → **New Project** → name it (e.g. `engagement-tracker`) → **Create**.
3. Make sure that new project is selected in the dropdown for every step below.

## 2. Enable the two APIs
1. Left menu → **APIs & Services** → **Library**.
2. Search **"Gmail API"** → open it → **Enable**.
3. Back to Library → search **"Google Sheets API"** → open → **Enable**.

## 3. Configure the OAuth consent screen (needed before the Gmail client)
1. **APIs & Services** → **OAuth consent screen** (may appear as **Google Auth Platform → Branding / Audience**).
2. User type: **External** → **Create**.
3. Fill **App name**, **User support email**, **Developer contact email** → **Save and continue**.
4. **Scopes**: skip (the tool requests Gmail read/modify/compose scopes at runtime) → **Save and continue**.
5. **Test users**: **Add users** → add the Gmail address that will receive the report emails → **Save**.
   *(Leaving the app in "Testing" is fine — only your test users can authorize it.)*

## 4. Create the Gmail OAuth client → `client_secret.json`
1. **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth client ID**.
2. **Application type: Desktop app** → name it → **Create**.
3. In the dialog → **Download JSON**.
4. Save it as: `engagement-tracker/secrets/client_secret.json`.

## 5. Create the Sheets service account → `service-account.json` + email
1. **APIs & Services** → **Credentials** → **Create Credentials** → **Service account**.
2. Name it (e.g. `sheet-writer`) → **Create and continue** → (no roles needed) → **Done**.
3. Click the new service account → **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create** (downloads the file).
4. Save it as: `engagement-tracker/secrets/service-account.json`.
5. Copy the service account's **email** (shown on its details page, ends in
   `…iam.gserviceaccount.com`) — **send me this address.**

## 6. Gmail label
1. In Gmail, create a label named **`tracking-reports`**.
2. Apply it to the report emails (one click, or a filter that auto-labels them).

## 7. Sheet copy + share
1. Open **`2026 Print Status Report`** → **File → Make a copy** (this is the safe trial target).
2. Open the copy → **Share** → paste the service-account **email** from step 5 → role **Editor** → send.
3. From the copy's URL, copy the **spreadsheet ID**
   (`https://docs.google.com/spreadsheets/d/`**`<THIS PART>`**`/edit`) and note the **tab name**.
4. **Send me:** the spreadsheet ID + tab name.

## 8. Contact CSV for report drafts
1. Copy `contacts.example.csv` to `contacts.csv`, or after processed sends
   exist, run `python -m tracking.cli contacts-init` to create a local starter
   file from the processed client names.
2. Fill one row per client with `client`, `pc_email`, and
   `report_delivery_enabled`. `contacts-init` starts every row as disabled
   (`report_delivery_enabled=no`) with a blank PC email; change those only after
   recipient review.
3. When later processed sends introduce new clients, run
   `python -m tracking.cli contacts-init --add-missing` to append only missing
   disabled rows without changing reviewed contacts.
4. Do not add lead-score, HIPAA, or secure-routing columns here; those are a
   later workflow and are rejected by the engagement draft validator.
5. Keep the real `contacts.csv` local; it is git-ignored.

## 9. Unattended runner
1. From the repo root, run `.\scripts\install_scheduled_task.ps1`.
2. It registers `EngagementTracker` every 4 minutes using `wscript.exe` and the
   hidden wrapper at `scripts/run_scheduled_hidden.vbs`.
3. Leave drafts off until contact routing has been checked. Then rerun with
   `.\scripts\install_scheduled_task.ps1 -Drafts`.
4. Use `python -m tracking.cli status` to review pending JobIDs, processed
   folders, existing draft IDs, and draft-readiness blockers without reading the
   full run log. Use `python -m tracking.cli status --json` if Power Automate
   or another wrapper needs machine-readable counts and blocker details.
   Scheduled runs also refresh `logs/status.json` with that same JSON snapshot.

---

## What to hand back to me
- ✅ `secrets/client_secret.json` and `secrets/service-account.json` saved locally (don't send the files).
- ✉️ The service-account **email** (`…iam.gserviceaccount.com`).
- ✉️ The **sheet copy ID** + **tab name**.
- ✅ Confirm the report-email **subject** is the `Client - Season Year Type` form (or send one sample subject).
- ✅ `contacts.csv` filled for any client that should receive draft report emails.

Then run `python -m tracking.cli authorize`, `pull`, `write --commit`,
`draft-reports --dry-run`, optionally
`draft-reports --dry-run --prepare-files`, and `draft-reports`. Install the
scheduled task only after those commands work manually. Use `run --drafts` or
`install_scheduled_task.ps1 -Drafts` only after `python -m tracking.cli status`
reports draft readiness.

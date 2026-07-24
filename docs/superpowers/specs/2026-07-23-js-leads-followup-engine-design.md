# JS Leads Follow-up Engine — Design & Builder Handoff

**Date:** 2026-07-23
**Status:** Design locked, ready for implementation planning
**Scope:** A reusable, campaign-agnostic follow-up engine for John Weaver's lead outreach. The current ~5–10 active leads are simply the first batch through it; nothing is hardcoded to today's list.

---

## 1. Purpose & guiding principle

**Problem being solved:**
- Emails filed into Outlook subfolders hide replies → John misses responses.
- The 3-day cadence and attempt counts depend on memory → follow-ups slip.
- Previous attempts had an AI *eyeball* Outlook to count sends → it miscounted repeatedly (couldn't tell sent from unsent drafts, re-scanned inconsistently).

**Core principle:** *Judgment stays with John; clerical counting leaves.* The system carries folders, dates, attempt counts, and overdue detection. John carries: is this lead worth pursuing, review/send drafts, interpret replies, decide resolved/disqualified.

**Safety principle:** *Fail closed — never guess its way into another email.* When uncertain, the system pauses outreach and surfaces the item for review rather than sending.

---

## 2. Architecture — four parts, one job each

| Part | Job | Implementation |
|------|-----|----------------|
| **Active Queue** | Single live source of truth (attempt #, last contact, next due, status) | New tab in the existing Google Sheet |
| **Morning script** | The clerical engine — counting, dates, spotting filed replies, briefing | Deterministic code (Python or PowerShell), **no AI**, scheduled on John's PC |
| **Agent, on demand** | Judgment help — draft the due emails, interpret an ambiguous reply | Triggered by John when he reviews the briefing |
| **JS Monthly report** | Chandra's clean monthly record | Updated **incrementally** from the queue (never regenerated) |

**Send-is-the-mark (the key idea):** John never clicks "I sent this." Sending the drafted email *is* the mark — the next morning's scan finds it in Sent Items and advances the queue automatically. The only manual confirmations are *judgment* calls (resolve / disqualify / snooze), which the briefing pre-fills. This removes the "I forgot to mark it" failure mode entirely.

---

## 3. Data model

**A. Active Queue tab** (live truth) — one row per active lead:
`lead_id | org | contact_email | attempt# | last_contact_date | next_due_date | status | notes`

**B. Durable event ledger** (append-only log, local + persisted) — the audit trail:
- Every detected Sent Item's **internet Message-ID** (dedupe key — see §8).
- Snooze history (each defer: until-date, reason, source, timestamp).
- Reply/bounce/departed detections with the matched message-id and match method.
- Run heartbeats ("last successful run" timestamps).

**C. Do Not Contact suppression list** (durable, cross-campaign, authoritative) — one row per suppression:
`scope (contact|organization) | key (email or org/domain) | reason | source | date | hard_stop (bool)`

**D. JS Monthly report cell-map** — mapping from `lead_id` → specific report row/cells, used for incremental writes (see §13).

---

## 4. Cadence rules

- **3 attempts, every 3 calendar days.** Due dates roll to the next weekday for the actual send (avoid weekend sends).
- A **reply stops the clock** immediately → the lead moves to Needs Review.
- After **attempt 3 + 3 days with no reply**, the system **proposes** disqualification (writes sheet flag **J**). **Never auto-disqualifies** — John confirms.
- **What consumes an attempt:** *only* a delivered send that receives no reply within the window. Unsent drafts, bounced sends, "contact departed" auto-replies, and OOO auto-replies **do not** consume an attempt.
- **Cadence config lives in constants** (attempt count, interval days) so a future campaign with a different cadence is a one-line change.

---

## 5. The daily loop

1. **~7:30am** — the scheduled script runs on John's PC.
2. It reads the Active Queue and reads Outlook (Sent Items + every folder).
3. It runs each active lead through the **ordered safety gate** (§6): matches Sent Items to advance attempts, matches inbound mail to detect replies/bounces/departures, computes due/overdue.
4. It refreshes one native Outlook **Search Folder** defined by **category = `JS - Needs Review`** (not by unread status — see §6 note), so filed replies can't hide.
5. It writes derived facts back to the queue + ledger and **emails John a briefing** (§12).
6. John reads it, says **"draft today's due ones,"** the agent creates Outlook drafts, John reviews and **sends**.
7. **Tomorrow's run sees those sends and advances the queue by itself.**

**Second safety check:** when John says "draft today's due ones," the agent **rescans Outlook immediately** — a reply that arrived *after* the morning run still blocks the draft.

---

## 6. The ordered safety gate (the heart of the system)

Evaluated top-down every run, for every lead, across every campaign. **First match decides the lead's state** for that run. A higher gate always wins over a lower one.

| # | Gate | Outcome | Consumes attempt? |
|---|------|---------|:---:|
| **0** | **Do Not Contact** | Hard stop, permanent, cross-campaign. No draft / propose / enroll. Checked before anything else. | — |
| **1** | **Human reply** (genuine) | Stop cadence; label `JS - Reply`; agent pre-tags intent (Positive / Decline / Defer) as a *suggestion*; → Needs Review. Never auto-acts. | no |
| **2** | **Contact departed** | Halt; flag address non-deliverable; → Needs Review "need new contact." | no |
| **3** | **Bounce** | Hard → halt + flag address dead + Needs Review. Soft → **Retry Pending** (surfaced for John; script **never auto-resends**); repeated soft → escalate to hard. | no |
| **4** | **Out of office** | Snooze to return date (requires until-date + reason; fallback +7d, flagged). Attempt not advanced. On wake → Needs Review. | no |
| **5** | **Ambiguous match** | Fail closed: pause; label `JS - Check` (separate queue from real replies); → Needs Review with the reason it's uncertain. | no |
| **6** | **No inbound → cadence** | Before due: wait. Due: propose next attempt (draft). After attempt 3 + 3d, no reply: propose Disqualify (sheet **J**). | **yes** (only here) |

**Why Human reply (Gate 1) sits above the delivery signals (Gates 2–3):** a genuine reply is the highest-value non-compliance signal. A bounce on a *parallel or later* attempt must not bury a real reply that already arrived on an earlier one (the lead replied from a working address; the bounced one is moot).

**Note on the search folder — category, not unread:** the script applies the umbrella Outlook category **`JS - Needs Review`** to *every* item the folder should surface (replies, ambiguous, departed, bad-address, retry-pending). The search folder filters on that **one category**, never on read state (preview panes and rules can mark mail read). The finer **type** is recorded as the queue **status** (`Reply` / `Check` / `Departed` / `BadAddress` / `RetryPending`) and, optionally, a secondary category (`JS - Reply` / `JS - Check`) for at-a-glance color in Outlook. An item leaves the folder when John's resolution clears the umbrella category.

### Locked interpretations
- **Soft bounce → Retry Pending only.** It never causes the morning script to resend automatically (the script never sends — John does).
- **Snooze history lives in the queue/event ledger.** Sheet columns **N** (On hold) and **O** (next follow-up date) receive only the *current* reporting state, not the history.

### State exits (every state resolves to one of these, and writes the sheet)
- **Re-arm active** → new due date.
- **Snooze** → sheet **N = 1**, **O = until-date** (current state); history to ledger.
- **Closed / became client** → sheet **Q/R**.
- **Disqualified (3× no reply)** → sheet **J**.
- **Do Not Contact** → suppression list (permanent).

### Small outcome clarifications
- **DNC is lifted only manually** — if an opted-out person re-engages, it surfaces as Needs Review for John to lift; never an auto-resume.
- **A Retry-Pending resend keeps the same attempt number** — a soft-bounced attempt 2, once resent successfully, is still attempt 2 with a fresh +3 due date, not attempt 3.

---

## 7. The five reply-handling upgrades (full definitions)

**1. Reply intent triage.** On a human reply matched via the layered matcher (§9): stop cadence, clear next-due, block drafts, queue status `Reply` (umbrella category `JS - Needs Review`; optional secondary category `JS - Reply`). The on-demand agent pre-classifies likely intent — **Positive** (interested → recommend pass to Chandra), **Decline** (not interested → recommend close), **Defer** (later → recommend Snooze) — as a *suggestion only*. Kept separate from ambiguous-match items (`JS - Check`) so the review queue is triaged by the decision required. Never auto-acts.

**2. Snooze.** Requires an **until-date** AND a **reason** (both mandatory). Suppresses reminders and drafts until the until-date, then moves to **Needs Review — not directly to Due** (John re-confirms it's still worth pursuing). Writes sheet **N + O** for current state; history to ledger. OOO auto-snooze uses the parsed return date (fallback +7d, flagged), reason `OOO until <date>`.

**3. Do Not Contact.** Highest-priority, cross-campaign hard stop, checked before drafting or proposing any outreach. Stores **scope** (`contact`|`organization`), **reason**, **source**, **date**, and an optional **`hard_stop`** flag. Permanent suppression, distinct from disqualification. Kept in a dedicated durable suppression list (not just a sheet flag). **Importing a new campaign must never reactivate it** — at import, any lead matching a DNC entry is suppressed on arrival, never queued. Policy: see §8.

**4. Contact departed.** "No longer with the organization" auto-replies are neither a bounce nor a human reply. Stop emailing the departed address (attempt not advanced, drafts blocked); → Needs Review "contact departed, needs new contact." John supplies a new contact (new row → fresh cadence) or closes. Flag the old address non-deliverable.

**5. Hard/soft bounce split.** Matched to a sent attempt. **Hard** (no such address / permanent) → halt, flag address dead, Needs Review "bad address." **Soft** (temporary) → **Retry Pending** (no auto-resend); if it soft-bounces again on a later John-driven retry, escalate to hard. Uses the same hard/soft/block taxonomy already produced in the engagement-report CSVs. A bounced send never consumes an attempt.

---

## 8. Do Not Contact policy (final)

> A DNC entry **blocks all outbound-initiated contact** — cadence sends, drafts, proposals, and re-import/enrollment into any campaign — **permanently and cross-campaign.** It does **not** block replying if the person emails John first, and it does not touch Chandra's separately-owned relationship. **Proactive-outreach-only** semantics ("stop soliciting me").

- **`hard_stop = true`** escalates an individual entry to **absolute** (no contact of any kind, including replies to their inbound) for hostile "never contact me again" cases.
- **`scope = contact`** → blocks proactive outreach to that individual only; a different, willing contact at the same org may still be pursued.
- **`scope = organization`** → blocks proactive outreach to anyone at that org/domain.

---

## 9. Reply/send attribution — layered matching

Priority order (first confident match wins; never match on domain alone):
1. Outlook **ConversationID / ConversationIndex** → a queued sent item.
2. **In-Reply-To / References** headers → the sent message's Message-ID.
3. Exact **sender address** == queue `contact_email`.
4. **Recipient + subject + date** corroboration.
5. **Domain-only → ambiguity warning only** (routes to Gate 5, never an auto-match).

**Builder caveat:** reaching internet headers over Outlook COM requires `PropertyAccessor` on `PR_TRANSPORT_MESSAGE_HEADERS` (`http://schemas.microsoft.com/mapi/proptag/0x007D001E`). Flag this early — it's easy to miss.

---

## 10. Idempotency & reliability

- **Dedupe on the internet Message-ID** (`PR_INTERNET_MESSAGE_ID`), *not* `EntryID` (EntryID changes when an item moves stores). A Sent Item whose Message-ID is already in the ledger is skipped — the same email is never counted twice.
- **Once-per-day guard:** the logon/unlock triggers (§11) must not re-process a day the 7:30 run already completed. The Message-ID ledger makes a double-run harmless even if it happens.
- **Silent-failure guard:** a failure *before* Outlook opens can't email an error. Mitigate with a **local log** and a **"last successful run: <timestamp>"** line in *every* briefing — a stale timestamp is the visible signal something broke.
- **Verify state via Sheets API readback**, not browser scraping (prior Windows automation couldn't reliably read Chrome's URL).
- **Do all date math in a single fixed local timezone** so a run near midnight can't shift a due date by a day.

---

## 11. Scheduling (handles sleeping / offline PC)

Windows Task Scheduler:
- Trigger at **7:30am**, with **"Run task as soon as possible after a scheduled start is missed."**
- Additional triggers: **At log on** and **On workstation unlock**.
- Apply the **once-per-day guard** (§10) so the extra triggers don't double-process.

---

## 12. The briefing email

Emailed to John's own inbox each morning (so it lands in Outlook, where he already looks). Contents:
- **Due today** (with attempt number) and **overdue**.
- **Needs Review** queue, split by type: replies (`JS - Reply`, with the agent's intent suggestion), ambiguous (`JS - Check`), contact-departed, bad-address.
- **Retry Pending** (soft bounces awaiting John's resend).
- **Snoozes waking today** (moved to Needs Review).
- **Proposed disqualifications** (attempt 3 + 3d, no reply) — pre-filled for confirmation.
- **Filed replies caught** (reply found in a subfolder).
- **"Last successful run: <timestamp>"** heartbeat line.
- Never sends anything automatically; drafts are prepared only on John's "draft today's due ones."

---

## 13. Incremental report sync (JS Monthly)

- **Never regenerate** the report — update only mapped cells, preserving formatting and manual notes.
- Locate the row by a **stable `lead_id`** (not org name), via the cell-map (§3D).
- **Prepend** the newest outreach date to the multi-date cell (newest-first, per Chandra).
- Watch Google Sheets **serial-date display** (e.g. `46176` → force explicit `M/D/YY` with the year).
- **N / O carry current state only**; snooze history stays in the ledger.

---

## 14. Environment & technical facts

- **Google Sheet:** ID `1OVR4kMck9_aBJ380pL_WqG-vhTglvnkn0TFLvM5617U`. Existing report tab **"My Outreach"**; the **Active Queue** is a new tab in the same sheet.
- **Existing report columns:** A=lead count, B=type, C=state, D=org, E=date in, F=John follow-up date, G=needs-analysis call, H/I=other dates, **J–N status flags** (J=no reply after 3 attempts, K=needs-analysis not scheduled, L=passed-to-Chandra YES, M=passed-to-Chandra NO, **N=On hold**), **O=on-hold next follow-up date**, P=passed-to-Chandra active, Q/R=became client yes/no, S/T=lead from ET email/reminder.
- **Outlook** (account `johnweaver@pentera.com`) is reachable **only via local desktop COM** on John's PC — no cloud/scheduled-cloud agent can reach it. COM recipe: `$ol = New-Object -ComObject Outlook.Application; $ns = $ol.GetNamespace("MAPI")`; folders `GetDefaultFolder(6)`=Inbox, `5`=Sent, `16`=Drafts; search via `.Items.Restrict("@SQL=…subject/textdescription LIKE…")`; draft via `$m = $ol.CreateItem(0); $m.BodyFormat = 1; …; $m.Save()`.
- The connected claude.ai **Gmail MCP is a different, personal mailbox** — it does **not** contain the Pentera lead correspondence. Do not use it here.

---

## 15. Prerequisites & open risks

1. **Sheet write credential (must verify).** There are two different credentials: the interactive claude.ai Google connector (has written successfully) and the standalone service account `trackingreposerv@trackrepo-498218.iam.gserviceaccount.com` (returned 403 / view-only in June). The **scheduled morning script runs headless and cannot do interactive OAuth**, so it must use a non-interactive credential (the service account or its own key). **The connector's success does not transfer to the script.** → *Confirm which credential the task uses; test a write; grant Editor if it 403s.*
2. **PropertyAccessor for headers** (§9) — verify header access works over COM before relying on In-Reply-To matching.
3. **Node + plugin are per-machine** — if John also runs this from the "dwaynetharock" station, Superpowers and Node must be installed there separately (see §17 handoff note); plugins are not account-synced.

---

## 16. Build order

1. Share the sheet as **Editor** with the script's credential; create the **Active Queue** tab, the **event ledger**, and the **DNC suppression list**.
2. Build the **deterministic scan + briefing script**; run it **manually** first and prove the counting is correct against the current batch (the historically fragile part).
3. **Schedule** it (§11).
4. Wire the **on-demand drafting** step (agent reads "due today," pulls thread context, creates Outlook drafts).

Every piece reads from the queue, never a fixed list — that's what makes it a standing, campaign-agnostic tool.

---

## 17. Division of responsibility

| John (judgment) | The machine (clerical) |
|-----------------|------------------------|
| Decide a lead is worth pursuing (add to queue) | Count attempts, compute due/overdue dates |
| Review and send prepared drafts | Spot replies filed in any folder |
| Interpret replies where intent is unclear | Advance the queue from Sent Items (send-is-the-mark) |
| Confirm resolve / disqualify / snooze / DNC | Enforce the ordered safety gate + DNC before any send |
| Handle relationship-sensitive messages | Update the queue + incrementally sync the report |

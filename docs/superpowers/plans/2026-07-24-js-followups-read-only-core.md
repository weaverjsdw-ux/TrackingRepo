# JS Follow-ups — Read-Only Core Implementation Plan (Plan 1 of a series)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a deterministic, read-only morning briefing of lead follow-up state (per lead: attempt count, last-sent, next-due, overdue/due/waiting, and any reply filed in any Outlook folder) — the reliable counting core the rest of the engine builds on.

**Architecture:** A PowerShell-STA helper (`outlook_scan.ps1`) is the *only* component that touches Outlook COM; it emits JSON. All decision logic lives in pure, unit-testable Python (`src/tracking/followups/`) that consumes that JSON. This isolates the one flaky boundary (live Outlook) and makes the logic that historically miscounted fully fixture-testable with no Outlook dependency.

**Tech Stack:** Python 3.14 (existing `tracking` package, pytest), Windows PowerShell 5.1 (`powershell.exe -STA`) for Outlook COM. No new Python dependencies (stdlib `json`, `subprocess`, `dataclasses`, `datetime`).

## Global Constraints

- **Read-only.** No drafts, no sends, no Outlook writes, no sheet writes anywhere in this plan.
- **Outlook COM runs only under `powershell.exe -STA`** — never pwsh 7 (MTA hangs). [Spike Finding A]
- **Resolve Exchange senders/recipients to canonical SMTP** (`AddressEntry.GetExchangeUser().PrimarySmtpAddress`, fallback `PR_SMTP_ADDRESS` `0x39FE001F`); lowercase all addresses before comparison. [Findings B, C]
- **Group and count by canonical recipient SMTP, across all attempts regardless of subject** (a reply-style `RE:` send still counts). [Findings C, D]
- **Cadence:** attempt interval = 3 calendar days (`next_due = last_sent + 3d`). Weekend-roll of the *send* date is out of scope for this plan (belongs to the sending phase).
- **Lead set is an explicit input** (allowlist of addresses/domains) for this plan; later plans replace it with the Active Queue. Do not read or write any Google Sheet here.
- Follow existing repo conventions: package under `src/tracking/`, tests as `tests/test_*.py`, frequent commits.

---

## File Structure

- Create `src/tracking/followups/__init__.py` — new subpackage.
- Create `src/tracking/followups/model.py` — dataclasses: `SentRecord`, `ReplyHit`, `LeadThread`.
- Create `src/tracking/followups/cadence.py` — pure functions `next_due`, `compute_status`.
- Create `src/tracking/followups/scan.py` — `build_threads(records, replies)` grouping/counting logic.
- Create `src/tracking/followups/briefing.py` — `render_briefing(threads, today)` text output.
- Create `src/tracking/followups/collect.py` — `run_outlook_scan(...)` subprocess wrapper + JSON parse to `SentRecord`/`ReplyHit`.
- Create `scripts/outlook_scan.ps1` — PowerShell-STA Outlook enumerator emitting JSON.
- Create `src/tracking/followups/cli.py` — `main()` wiring collect → build → render → print.
- Tests: `tests/test_followups_cadence.py`, `tests/test_followups_scan.py`, `tests/test_followups_briefing.py`, `tests/fixtures/followups/scan_sample.json`.

---

### Task 1: Data model + cadence functions

**Files:**
- Create: `src/tracking/followups/__init__.py` (empty)
- Create: `src/tracking/followups/model.py`
- Create: `src/tracking/followups/cadence.py`
- Test: `tests/test_followups_cadence.py`

**Interfaces:**
- Produces: `SentRecord(conversation_id:str, recipient_smtp:str, recipient_domain:str, sent_on:datetime, message_id:str, subject:str)`; `ReplyHit(from_domain:str, folder:str, received:datetime)`; `LeadThread(recipient_smtp:str, attempts:int, last_sent:datetime, conversation_ids:list[str], reply:ReplyHit|None)`; `next_due(last_sent:datetime, interval_days:int=3)->date`; `compute_status(thread:LeadThread, today:date)->str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_followups_cadence.py
from datetime import datetime, date
from tracking.followups.model import LeadThread, ReplyHit
from tracking.followups.cadence import next_due, compute_status

def test_next_due_is_three_calendar_days():
    assert next_due(datetime(2026, 7, 17, 9, 0)) == date(2026, 7, 20)

def test_status_overdue_when_today_past_due():
    t = LeadThread("a@x.org", 1, datetime(2026, 7, 17), ["c1"], None)
    assert compute_status(t, date(2026, 7, 24)) == "OVERDUE"

def test_status_due_today():
    t = LeadThread("a@x.org", 1, datetime(2026, 7, 17), ["c1"], None)
    assert compute_status(t, date(2026, 7, 20)) == "DUE TODAY"

def test_status_waiting_before_due():
    t = LeadThread("a@x.org", 1, datetime(2026, 7, 17), ["c1"], None)
    assert compute_status(t, date(2026, 7, 18)) == "waiting"

def test_reply_overrides_cadence():
    t = LeadThread("a@x.org", 1, datetime(2026, 7, 17), ["c1"],
                   ReplyHit("x.org", "Inbox", datetime(2026, 7, 19)))
    assert compute_status(t, date(2026, 7, 24)) == "NEEDS REVIEW (reply)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_cadence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracking.followups'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/tracking/followups/model.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass
class SentRecord:
    conversation_id: str
    recipient_smtp: str
    recipient_domain: str
    sent_on: datetime
    message_id: str
    subject: str

@dataclass
class ReplyHit:
    from_domain: str
    folder: str
    received: datetime

@dataclass
class LeadThread:
    recipient_smtp: str
    attempts: int
    last_sent: datetime
    conversation_ids: list[str]
    reply: "ReplyHit | None" = None
```

```python
# src/tracking/followups/cadence.py
from __future__ import annotations
from datetime import datetime, timedelta, date
from .model import LeadThread

def next_due(last_sent: datetime, interval_days: int = 3) -> date:
    return (last_sent + timedelta(days=interval_days)).date()

def compute_status(thread: LeadThread, today: date) -> str:
    if thread.reply is not None:
        return "NEEDS REVIEW (reply)"
    due = next_due(thread.last_sent)
    if today > due:
        return "OVERDUE"
    if today == due:
        return "DUE TODAY"
    return "waiting"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_cadence.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git -C engagement-tracker add src/tracking/followups/__init__.py src/tracking/followups/model.py src/tracking/followups/cadence.py tests/test_followups_cadence.py
git -C engagement-tracker commit -m "feat(followups): data model + cadence status functions"
```

---

### Task 2: Thread building (canonical grouping + conversation-based counting)

**Files:**
- Create: `src/tracking/followups/scan.py`
- Test: `tests/test_followups_scan.py`

**Interfaces:**
- Consumes: `SentRecord`, `ReplyHit`, `LeadThread` from Task 1.
- Produces: `build_threads(records: list[SentRecord], replies: dict[str, ReplyHit]) -> list[LeadThread]` — groups records by `recipient_smtp`, sets `attempts` = count of records for that recipient, `last_sent` = max `sent_on`, `conversation_ids` = sorted unique, and attaches `replies.get(recipient_smtp)`. Returns threads sorted by `recipient_smtp`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_followups_scan.py
from datetime import datetime
from tracking.followups.model import SentRecord, ReplyHit
from tracking.followups.scan import build_threads

def _rec(smtp, when, conv, subj="Following up"):
    return SentRecord(conv, smtp, smtp.split("@")[-1], when, f"<{when.isoformat()}@x>", subj)

def test_same_recipient_two_sends_counts_two():
    # Finding C/D: display-name vs SMTP already canonicalized upstream to the same smtp;
    # two sends (any subject) => attempts == 2, newest last_sent.
    recs = [
        _rec("jaltchek@parkschool.net", datetime(2026, 7, 17), "cA", "Following up"),
        _rec("jaltchek@parkschool.net", datetime(2026, 7, 20), "cA", "RE: Following up"),
    ]
    threads = build_threads(recs, {})
    assert len(threads) == 1
    t = threads[0]
    assert t.attempts == 2
    assert t.last_sent == datetime(2026, 7, 20)
    assert t.conversation_ids == ["cA"]

def test_distinct_recipients_are_separate_threads_sorted():
    recs = [
        _rec("b@z.org", datetime(2026, 7, 17), "c1"),
        _rec("a@y.org", datetime(2026, 7, 18), "c2"),
    ]
    threads = build_threads(recs, {})
    assert [t.recipient_smtp for t in threads] == ["a@y.org", "b@z.org"]

def test_reply_attached_by_recipient():
    recs = [_rec("a@y.org", datetime(2026, 7, 17), "c1")]
    reply = ReplyHit("y.org", "Leads", datetime(2026, 7, 19))
    threads = build_threads(recs, {"a@y.org": reply})
    assert threads[0].reply is reply
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracking.followups.scan'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/tracking/followups/scan.py
from __future__ import annotations
from .model import SentRecord, ReplyHit, LeadThread

def build_threads(records: list[SentRecord],
                  replies: "dict[str, ReplyHit]") -> list[LeadThread]:
    by_recipient: "dict[str, list[SentRecord]]" = {}
    for r in records:
        by_recipient.setdefault(r.recipient_smtp.lower(), []).append(r)
    threads: list[LeadThread] = []
    for smtp, recs in by_recipient.items():
        recs.sort(key=lambda r: r.sent_on)
        conv_ids = sorted({r.conversation_id for r in recs})
        threads.append(LeadThread(
            recipient_smtp=smtp,
            attempts=len(recs),
            last_sent=recs[-1].sent_on,
            conversation_ids=conv_ids,
            reply=replies.get(smtp),
        ))
    threads.sort(key=lambda t: t.recipient_smtp)
    return threads
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_scan.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C engagement-tracker add src/tracking/followups/scan.py tests/test_followups_scan.py
git -C engagement-tracker commit -m "feat(followups): thread building with canonical grouping + attempt counts"
```

---

### Task 3: Briefing renderer

**Files:**
- Create: `src/tracking/followups/briefing.py`
- Test: `tests/test_followups_briefing.py`

**Interfaces:**
- Consumes: `LeadThread` (Task 1), `compute_status`/`next_due` (Task 1).
- Produces: `render_briefing(threads: list[LeadThread], today: date) -> str`. Lines per lead: `"- {smtp}\n    attempts: {n}   last sent: MM/DD   next due: MM/DD   -> {status}"`; append `"    {reply}"` when a reply exists. Header line: `"JS Follow-ups — {today:%a %m/%d/%Y} — {len} threads"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_followups_briefing.py
from datetime import datetime, date
from tracking.followups.model import LeadThread, ReplyHit
from tracking.followups.briefing import render_briefing

def test_render_contains_status_and_dates():
    threads = [LeadThread("a@y.org", 2, datetime(2026, 7, 17), ["c1"], None)]
    out = render_briefing(threads, date(2026, 7, 24))
    assert "JS Follow-ups — Fri 07/24/2026 — 1 threads" in out
    assert "- a@y.org" in out
    assert "attempts: 2" in out
    assert "last sent: 07/17" in out
    assert "next due: 07/20" in out
    assert "-> OVERDUE" in out

def test_render_shows_reply_line():
    r = ReplyHit("y.org", "Leads", datetime(2026, 7, 19, 8, 30))
    threads = [LeadThread("a@y.org", 1, datetime(2026, 7, 17), ["c1"], r)]
    out = render_briefing(threads, date(2026, 7, 24))
    assert "NEEDS REVIEW (reply)" in out
    assert "y.org" in out and "Leads" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_briefing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracking.followups.briefing'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/tracking/followups/briefing.py
from __future__ import annotations
from datetime import date
from .model import LeadThread
from .cadence import next_due, compute_status

def render_briefing(threads: list[LeadThread], today: date) -> str:
    lines = [f"JS Follow-ups — {today:%a %m/%d/%Y} — {len(threads)} threads",
             "=" * 60]
    for t in threads:
        status = compute_status(t, today)
        due = next_due(t.last_sent)
        lines.append(f"- {t.recipient_smtp}")
        lines.append(
            f"    attempts: {t.attempts}   last sent: {t.last_sent:%m/%d}"
            f"   next due: {due:%m/%d}   -> {status}"
        )
        if t.reply is not None:
            lines.append(
                f"    REPLY from {t.reply.from_domain} in [{t.reply.folder}] {t.reply.received:%m/%d %H:%M}"
            )
    lines.append("=" * 60)
    lines.append("(read-only: no drafts, no writes, no marks changed)")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_briefing.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C engagement-tracker add src/tracking/followups/briefing.py tests/test_followups_briefing.py
git -C engagement-tracker commit -m "feat(followups): briefing text renderer"
```

---

### Task 4: Outlook scanner (PowerShell-STA) + JSON collector

**Files:**
- Create: `scripts/outlook_scan.ps1`
- Create: `src/tracking/followups/collect.py`
- Create: `tests/fixtures/followups/scan_sample.json`
- Test: `tests/test_followups_scan.py` (extend with a parse test)

**Interfaces:**
- Consumes: `SentRecord`, `ReplyHit` (Task 1).
- Produces: `parse_scan(payload: dict) -> tuple[list[SentRecord], dict[str, ReplyHit]]`; `run_outlook_scan(allowlist: list[str], days: int = 60, ps_path: str = "scripts/outlook_scan.ps1") -> tuple[list[SentRecord], dict[str, ReplyHit]]` (invokes `powershell.exe -STA`, parses stdout JSON via `parse_scan`).
- JSON contract (emitted by the ps1):
  ```json
  {"sent": [{"conversation_id":"..","recipient_smtp":"a@y.org","sent_on":"2026-07-17T09:00:00","message_id":"<..>","subject":".."}],
   "replies": [{"recipient_smtp":"a@y.org","from_domain":"y.org","folder":"Inbox","received":"2026-07-19T08:30:00"}]}
  ```

- [ ] **Step 1: Write the fixture and failing parse test**

```json
// tests/fixtures/followups/scan_sample.json
{"sent": [
  {"conversation_id":"cA","recipient_smtp":"jaltchek@parkschool.net","sent_on":"2026-07-17T09:00:00","message_id":"<1@x>","subject":"Following up"},
  {"conversation_id":"cA","recipient_smtp":"jaltchek@parkschool.net","sent_on":"2026-07-20T09:00:00","message_id":"<2@x>","subject":"RE: Following up"}
], "replies": [
  {"recipient_smtp":"jaltchek@parkschool.net","from_domain":"parkschool.net","folder":"Leads","received":"2026-07-21T10:15:00"}
]}
```

```python
# add to tests/test_followups_scan.py
import json, pathlib
from tracking.followups.collect import parse_scan

def test_parse_scan_builds_records_and_replies():
    payload = json.loads(
        pathlib.Path("tests/fixtures/followups/scan_sample.json").read_text()
    )
    records, replies = parse_scan(payload)
    assert len(records) == 2
    assert records[0].recipient_domain == "parkschool.net"
    assert "jaltchek@parkschool.net" in replies
    assert replies["jaltchek@parkschool.net"].folder == "Leads"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_scan.py::test_parse_scan_builds_records_and_replies -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tracking.followups.collect'`

- [ ] **Step 3: Write the collector**

```python
# src/tracking/followups/collect.py
from __future__ import annotations
import json, subprocess
from datetime import datetime
from .model import SentRecord, ReplyHit

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)

def parse_scan(payload: dict) -> "tuple[list[SentRecord], dict[str, ReplyHit]]":
    records = [
        SentRecord(
            conversation_id=s["conversation_id"],
            recipient_smtp=s["recipient_smtp"].lower(),
            recipient_domain=s["recipient_smtp"].split("@")[-1].lower(),
            sent_on=_dt(s["sent_on"]),
            message_id=s["message_id"],
            subject=s.get("subject", ""),
        )
        for s in payload.get("sent", [])
    ]
    replies = {
        r["recipient_smtp"].lower(): ReplyHit(
            from_domain=r["from_domain"].lower(),
            folder=r["folder"],
            received=_dt(r["received"]),
        )
        for r in payload.get("replies", [])
    }
    return records, replies

def run_outlook_scan(allowlist: list[str], days: int = 60,
                     ps_path: str = "scripts/outlook_scan.ps1"
                     ) -> "tuple[list[SentRecord], dict[str, ReplyHit]]":
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
         "-File", ps_path, "-Allowlist", ";".join(allowlist), "-Days", str(days)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"outlook_scan.ps1 failed: {proc.stderr.strip()[:400]}")
    return parse_scan(json.loads(proc.stdout))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd engagement-tracker && .venv/Scripts/python.exe -m pytest tests/test_followups_scan.py -v`
Expected: PASS (all scan tests)

- [ ] **Step 5: Write the PowerShell-STA scanner**

```powershell
# scripts/outlook_scan.ps1
param([string]$Allowlist = "", [int]$Days = 60)
$ErrorActionPreference = 'Stop'
$allow = @($Allowlist.ToLower().Split(';') | Where-Object { $_ })
$ol = New-Object -ComObject Outlook.Application
$ns = $ol.GetNamespace('MAPI')
$sent = $ns.GetDefaultFolder(5)
$since = (Get-Date).AddDays(-$Days)
$PR_SMTP = 'http://schemas.microsoft.com/mapi/proptag/0x39FE001F'
$PR_MSGID = 'http://schemas.microsoft.com/mapi/proptag/0x1035001F'

function Smtp-Of-Recipient($m) {
  try {
    $r = $m.Recipients.Item(1)
    if ($r.AddressEntry.AddressEntryUserType -eq 0 -or $r.AddressEntry.Type -eq 'EX') {
      $eu = $r.AddressEntry.GetExchangeUser(); if ($eu) { return $eu.PrimarySmtpAddress.ToLower() }
    }
    try { return ($r.PropertyAccessor.GetProperty($PR_SMTP)).ToLower() } catch {}
    return ("" + $r.Address).ToLower()
  } catch { return ("" + $m.To).ToLower() }
}
function Smtp-Of-Sender($m) {
  try {
    if ($m.SenderEmailType -eq 'EX' -and $m.Sender) {
      $eu = $m.Sender.GetExchangeUser(); if ($eu) { return $eu.PrimarySmtpAddress.ToLower() }
    }
  } catch {}
  return ("" + $m.SenderEmailAddress).ToLower()
}
function Domain-Of($a) { $s = "" + $a; if ($s -match '@') { return ($s.Split('@')[-1]) } return '' }
function In-Allow($smtp) {
  if ($allow.Count -eq 0) { return $true }
  $d = Domain-Of $smtp
  foreach ($a in $allow) { if ($smtp -eq $a -or $d -eq $a) { return $true } }
  return $false
}

$items = $sent.Items; $items.Sort('[SentOn]', $true)
$sentOut = New-Object System.Collections.ArrayList
$convByRecipient = @{}
foreach ($m in $items) {
  try {
    if ($m.Class -ne 43) { continue }
    if ($m.SentOn -lt $since) { break }
    $smtp = Smtp-Of-Recipient $m
    if (-not (In-Allow $smtp)) { continue }
    $mid = ""; try { $mid = $m.PropertyAccessor.GetProperty($PR_MSGID) } catch {}
    [void]$sentOut.Add([ordered]@{
      conversation_id = "" + $m.ConversationID
      recipient_smtp  = $smtp
      sent_on         = $m.SentOn.ToString('s')
      message_id      = $mid
      subject         = "" + $m.Subject
    })
    if (-not $convByRecipient.ContainsKey($smtp)) { $convByRecipient[$smtp] = @{} }
    $convByRecipient[$smtp][("" + $m.ConversationID)] = $m
  } catch {}
}

# reply detection across folders via Conversation tree
$replies = New-Object System.Collections.ArrayList
function Walk-Conv($conv, $item, $leadDom, [ref]$hit) {
  if ($hit.Value) { return }
  try { $kids = $conv.GetChildren($item) } catch { return }
  foreach ($k in $kids) {
    try {
      if ($k.Class -eq 43) {
        $d = Domain-Of (Smtp-Of-Sender $k)
        if ($d -and $d -eq $leadDom) {
          $fld = try { $k.Parent.Name } catch { '?' }
          $hit.Value = @{ folder = $fld; received = $k.ReceivedTime.ToString('s'); dom = $d }; return
        }
      }
    } catch {}
    Walk-Conv $conv $k $leadDom $hit
  }
}
foreach ($smtp in $convByRecipient.Keys) {
  $leadDom = Domain-Of $smtp
  $hit = [ref]$null
  foreach ($cid in $convByRecipient[$smtp].Keys) {
    $anchor = $convByRecipient[$smtp][$cid]
    try { $conv = $anchor.GetConversation(); if ($conv) { foreach ($r in $conv.GetRootItems()) { Walk-Conv $conv $r $leadDom $hit } } } catch {}
    if ($hit.Value) { break }
  }
  if ($hit.Value) {
    [void]$replies.Add([ordered]@{ recipient_smtp = $smtp; from_domain = $hit.Value.dom; folder = $hit.Value.folder; received = $hit.Value.received })
  }
}

[ordered]@{ sent = $sentOut; replies = $replies } | ConvertTo-Json -Depth 6
```

- [ ] **Step 6: Commit**

```bash
git -C engagement-tracker add scripts/outlook_scan.ps1 src/tracking/followups/collect.py tests/fixtures/followups/scan_sample.json tests/test_followups_scan.py
git -C engagement-tracker commit -m "feat(followups): STA Outlook scanner + JSON collector"
```

---

### Task 5: CLI wiring + live integration run

**Files:**
- Create: `src/tracking/followups/cli.py`
- Modify: `pyproject.toml` (add console entry `js-followups = "tracking.followups.cli:main"` under `[project.scripts]`)

**Interfaces:**
- Consumes: `run_outlook_scan` (Task 4), `build_threads` (Task 2), `render_briefing` (Task 3).
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the CLI**

```python
# src/tracking/followups/cli.py
from __future__ import annotations
import argparse, sys
from datetime import date
from .collect import run_outlook_scan
from .scan import build_threads
from .briefing import render_briefing

def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only JS follow-ups briefing")
    ap.add_argument("--allow", default="",
                    help="semicolon-separated lead SMTP addresses or domains")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--ps", default="scripts/outlook_scan.ps1")
    args = ap.parse_args(argv)
    allow = [a for a in args.allow.split(";") if a]
    records, replies = run_outlook_scan(allow, days=args.days, ps_path=args.ps)
    threads = build_threads(records, replies)
    print(render_briefing(threads, date.today()))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Live integration run (manual — requires Outlook)**

Run (current batch allowlist derived from the spike):
```bash
cd engagement-tracker && .venv/Scripts/python.exe -m tracking.followups.cli \
  --allow "sclerodermaresearch.org;mbayaq.org;parkschool.net;teamgleason.org;csulb.edu;familyresourcenetwork.org;robinhood.org" --days 60
```
Expected: a briefing listing each lead with attempts / last sent / next due / status, and `parkschool.net` appearing **once** (not split into `jaltchek@…` + `Jamie Altchek`). Verify counts by eye against Sent Items — this is the reliability gate.

- [ ] **Step 3: Commit**

```bash
git -C engagement-tracker add src/tracking/followups/cli.py pyproject.toml
git -C engagement-tracker commit -m "feat(followups): CLI entry for read-only briefing"
```

---

## Self-Review

- **Spec coverage (read-only slice):** deterministic scan (§5), layered/SMTP matching foundation (§9, Findings B/C), conversation-based counting (§4, Finding D), cross-folder reply detection (§6 Gate 1 / §9), briefing shape (§12) — all have tasks. Write path (§3 queue/ledger/DNC, §6 gates 2–6 writes, §13 report sync), scheduling (§11), and drafting (§7.1) are **explicitly deferred to Plans 2–5** and gated on the Editor credential fix (§15.1). No silent gaps.
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `SentRecord`/`ReplyHit`/`LeadThread` field names and `build_threads`/`parse_scan`/`render_briefing`/`run_outlook_scan` signatures are consistent across Tasks 1–5.
- **Known limitation to carry into Plan 2:** reply detection is wired and error-free but was not exercised against a *known positive* reply during spikes — Task 5's live run should be repeated once a real lead reply exists to confirm the Conversation walk catches it.

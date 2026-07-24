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

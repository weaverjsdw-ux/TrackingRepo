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

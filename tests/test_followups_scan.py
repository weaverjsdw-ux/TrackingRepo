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

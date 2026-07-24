# tests/test_followups_scan.py
import json, pathlib
from datetime import datetime
from tracking.followups.model import SentRecord, ReplyHit
from tracking.followups.scan import build_threads
from tracking.followups.collect import parse_scan

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

def test_parse_scan_builds_records_and_replies():
    payload = json.loads(
        pathlib.Path("tests/fixtures/followups/scan_sample.json").read_text()
    )
    records, replies = parse_scan(payload)
    assert len(records) == 2
    assert records[0].recipient_domain == "parkschool.net"
    assert "jaltchek@parkschool.net" in replies
    assert replies["jaltchek@parkschool.net"].folder == "Leads"

def test_parse_scan_single_element_serialized_as_object():
    # PS 5.1 ConvertTo-Json unwraps a 1-element collection into a bare object
    payload = {
        "sent": {"conversation_id": "cA", "recipient_smtp": "a@y.org",
                 "sent_on": "2026-07-17T09:00:00", "message_id": "<1@x>", "subject": "Following up"},
        "replies": {"recipient_smtp": "a@y.org", "from_domain": "y.org",
                    "folder": "Inbox", "received": "2026-07-19T08:30:00"},
    }
    records, replies = parse_scan(payload)
    assert len(records) == 1
    assert records[0].recipient_smtp == "a@y.org"
    assert replies["a@y.org"].folder == "Inbox"

def test_parse_scan_empty_or_null_results():
    assert parse_scan({"sent": None, "replies": None}) == ([], {})
    assert parse_scan({}) == ([], {})

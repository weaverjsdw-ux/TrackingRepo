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

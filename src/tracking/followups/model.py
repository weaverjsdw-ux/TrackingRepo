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

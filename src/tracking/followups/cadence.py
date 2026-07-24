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

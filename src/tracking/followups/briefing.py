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

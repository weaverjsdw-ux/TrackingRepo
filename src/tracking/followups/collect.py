# src/tracking/followups/collect.py
from __future__ import annotations
import json, subprocess
from datetime import datetime
from .model import SentRecord, ReplyHit

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)

def _as_list(v):
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    return v

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
        for s in _as_list(payload.get("sent"))
    ]
    replies = {
        r["recipient_smtp"].lower(): ReplyHit(
            from_domain=r["from_domain"].lower(),
            folder=r["folder"],
            received=_dt(r["received"]),
        )
        for r in _as_list(payload.get("replies"))
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

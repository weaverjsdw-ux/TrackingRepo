"""Persist operator-facing automation state.

This file is intentionally small and JSON-backed: it is local machine state,
not a database. It tracks pending JobIDs, completed sends, draft ids, and the
last run timestamp so scheduled runs can stay quiet unless something changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .intake import StagedSend

STATE_FILE = ".automation_state.json"


def default_state_path(drop_root: str | Path) -> Path:
    return Path(drop_root) / STATE_FILE


def _empty_state() -> dict[str, Any]:
    return {"last_run": None, "pending": {}, "processed": {}, "drafts": {}}


def load_state(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return _empty_state()
    state = json.loads(p.read_text(encoding="utf-8"))
    base = _empty_state()
    base.update(state)
    for key in ("pending", "processed", "drafts"):
        base.setdefault(key, {})
    return base


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


@dataclass
class PullStateUpdate:
    changed_pending: list[StagedSend] = field(default_factory=list)
    unchanged_pending_count: int = 0
    completed_count: int = 0


def record_staged(
    state_path: str | Path,
    staged: list[StagedSend],
    *,
    now: str | None = None,
) -> PullStateUpdate:
    """Record one pull cycle and return what changed enough to log."""
    timestamp = now or datetime.now().replace(microsecond=0).isoformat()
    state = load_state(state_path)
    update = PullStateUpdate()

    for item in staged:
        if item.pending_reason:
            previous = state["pending"].get(item.job_id)
            changed = previous is None or previous.get("reason") != item.pending_reason
            first_seen = previous.get("first_seen") if previous else timestamp
            seen_count = int(previous.get("seen_count", 0)) + 1 if previous else 1
            state["pending"][item.job_id] = {
                "reason": item.pending_reason,
                "first_seen": first_seen,
                "last_seen": timestamp,
                "seen_count": seen_count,
                "message_ids": list(item.message_ids),
                "folder_name": item.folder_name,
            }
            if changed:
                update.changed_pending.append(item)
            else:
                update.unchanged_pending_count += 1
            continue

        if item.identity is not None:
            state["pending"].pop(item.job_id, None)
            state["processed"][item.identity.folder_name] = {
                "job_id": item.job_id,
                "last_seen": timestamp,
                "message_ids": list(item.message_ids),
            }
            update.completed_count += 1

    state["last_run"] = timestamp
    save_state(state_path, state)
    return update


def remember_draft(
    state_path: str | Path,
    send_key: str,
    draft_id: str,
    *,
    now: str | None = None,
) -> None:
    timestamp = now or datetime.now().replace(microsecond=0).isoformat()
    state = load_state(state_path)
    state["drafts"][send_key] = {"draft_id": draft_id, "created_at": timestamp}
    save_state(state_path, state)


def draft_id_for(state_path: str | Path, send_key: str) -> str | None:
    entry = load_state(state_path)["drafts"].get(send_key)
    return entry.get("draft_id") if entry else None


def format_status(state_path: str | Path, *, processed_root: str | Path | None = None) -> str:
    state = load_state(state_path)
    lines = [f"Last run: {state.get('last_run') or 'never'}"]

    pending = state.get("pending", {})
    lines.append(f"Pending sends: {len(pending)}")
    for job_id, entry in sorted(pending.items()):
        message_count = len(entry.get("message_ids") or [])
        lines.append(
            f"  job {job_id}: {entry.get('reason')} "
            f"(first seen {entry.get('first_seen')}, last seen {entry.get('last_seen')}, "
            f"seen {entry.get('seen_count')}x, messages {message_count}, "
            f"folder {entry.get('folder_name')})"
        )

    processed = dict(state.get("processed", {}))
    if processed_root is not None:
        root = Path(processed_root)
        if root.is_dir():
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                processed.setdefault(
                    folder.name,
                    {"job_id": None, "last_seen": None, "folder_present": True},
                )
    lines.append(f"Processed sends: {len(processed)}")
    for name, entry in sorted(processed.items()):
        if entry.get("job_id"):
            lines.append(f"  {name}: job {entry.get('job_id')} (last seen {entry.get('last_seen')})")
        else:
            lines.append(f"  {name}: processed folder present")

    drafts = state.get("drafts", {})
    lines.append(f"Drafted reports: {len(drafts)}")
    for name, entry in sorted(drafts.items()):
        lines.append(f"  {name}: draft {entry.get('draft_id')} (created {entry.get('created_at')})")

    return "\n".join(lines)

"""Phase 2 intake — source-agnostic orchestration (BRIEF §1).

The puller reads a dedicated Gmail label, saves attachments into an internal
drop folder per send, and the Phase 1 pipeline processes that folder. This
module owns everything EXCEPT the Gmail wire protocol, which sits behind the
EmailSource interface so the orchestration is testable with a fake source and a
file can be hand-dropped for a manual re-run (BRIEF §1).

Responsibilities (BRIEF §1):
  * group one-or-several report emails into the send they belong to,
  * stage attachments into <drop_root>/inbox/<Client - Season Year Type>/,
  * dedup (a re-pulled message or a duplicate attachment is not re-saved),
  * tolerate out-of-order / delayed arrival (an incomplete send stays pending),
  * mark messages processed and move the completed folder to <drop_root>/processed/.

The actual Gmail client (OAuth/service account) is the EmailSource
implementation in gmail_source.py; it is intentionally NOT imported here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from . import naming
from .pipeline import SendResult, assess_completeness, process_folder

STATE_FILE = ".intake_state.json"


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes


@dataclass(frozen=True)
class EmailMessage:
    id: str
    subject: str
    attachments: tuple[Attachment, ...]


@runtime_checkable
class EmailSource(Protocol):
    """The Gmail seam. The real adapter and the test fake both satisfy this."""

    def fetch_labeled(self, label: str) -> list[EmailMessage]: ...

    def mark_processed(self, message_id: str) -> None: ...


@dataclass
class StagedSend:
    folder_name: str
    drop_folder: Path
    message_ids: list[str]
    result: SendResult | None = None  # set when processing succeeded
    pending_reason: str | None = None  # set when left pending (delayed/incomplete)
    log: list[str] = field(default_factory=list)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_state(drop_root: Path) -> dict:
    path = drop_root / STATE_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"processed_message_ids": []}


def _save_state(drop_root: Path, state: dict) -> None:
    (drop_root / STATE_FILE).write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )


def _save_dedup(drop_folder: Path, att: Attachment, seen: dict[str, str]) -> bool:
    """Write an attachment unless an identical one (by content hash) is already
    staged. Returns True if newly written. Different content with the same name
    is suffixed rather than overwritten (loud-by-data, never clobber)."""
    digest = _sha(att.data)
    if digest in seen:
        return False
    target = drop_folder / att.filename
    if target.exists() and _sha(target.read_bytes()) != digest:
        target = target.with_name(f"{target.stem}__{digest[:8]}{target.suffix}")
    drop_folder.mkdir(parents=True, exist_ok=True)
    target.write_bytes(att.data)
    seen[digest] = target.name
    return True


def pull_and_stage(
    source: EmailSource,
    label: str,
    drop_root: str | Path,
    *,
    subject_parser: Callable[[str], naming.SendIdentity] = naming.parse_send_identity,
    process: Callable[[Path], SendResult] = process_folder,
    completeness: Callable[[SendResult], tuple[bool, list[str]]] = assess_completeness,
) -> list[StagedSend]:
    """Pull labeled messages, stage per send, process complete sends, and move
    them to processed/. Idempotent: already-processed message ids are skipped,
    so re-running never double-files, double-marks, or re-processes (BRIEF §3).

    subject_parser maps an email subject -> SendIdentity. Default assumes the
    subject carries the 'Client - Season Year Type' form; the real convention is
    an open operator question (see README Phase 2 notes) and is a one-line swap.
    """
    drop_root = Path(drop_root)
    inbox = drop_root / "inbox"
    processed_root = drop_root / "processed"
    drop_root.mkdir(parents=True, exist_ok=True)

    state = _load_state(drop_root)
    done: set[str] = set(state["processed_message_ids"])

    # Group fresh messages by the send they belong to (out-of-order safe).
    groups: dict[str, list[EmailMessage]] = {}
    for msg in source.fetch_labeled(label):
        if msg.id in done:
            continue
        identity = subject_parser(msg.subject)
        groups.setdefault(identity.folder_name, []).append(msg)

    staged: list[StagedSend] = []
    for folder_name, msgs in groups.items():
        drop_folder = inbox / folder_name
        item = StagedSend(folder_name, drop_folder, [m.id for m in msgs])

        seen: dict[str, str] = {}
        for existing in drop_folder.glob("*") if drop_folder.exists() else []:
            if existing.is_file():
                seen[_sha(existing.read_bytes())] = existing.name
        for msg in msgs:
            for att in msg.attachments:
                if _save_dedup(drop_folder, att, seen):
                    item.log.append(f"staged {att.filename} (from {msg.id})")

        # Process, then gate on completeness. A still-incomplete send (delayed
        # arrival) is left pending and its messages are NOT marked done, so the
        # next pull picks up the rest. A hard processing error also pends.
        try:
            item.result = process(drop_folder)
        except Exception as exc:  # noqa: BLE001 - pending is an expected outcome
            item.pending_reason = str(exc)
            item.log.append(f"PENDING (processing error): {exc}")
            staged.append(item)
            continue

        complete, missing = completeness(item.result)
        if not complete:
            item.pending_reason = f"incomplete: missing {missing}"
            item.log.append(f"PENDING (not yet complete): missing {missing}")
            staged.append(item)
            continue

        for msg in msgs:
            source.mark_processed(msg.id)
            done.add(msg.id)
        dest = processed_root / folder_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(drop_folder), str(dest))
        item.drop_folder = dest
        item.log.append(f"processed and moved to {dest}")
        staged.append(item)

    state["processed_message_ids"] = sorted(done)
    _save_state(drop_root, state)
    return staged

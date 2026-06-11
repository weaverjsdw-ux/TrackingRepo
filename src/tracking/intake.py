"""Phase 2 intake — source-agnostic orchestration (BRIEF §1).

Reality of how reports arrive (confirmed against the live inbox): ExactTarget
sends ONE "Email Export" notification per file, each carrying in its body
`Exported File: …`, `Exported Type: <Send|Open|click|bounce|unsub>`, and
`JobID: <n>`. The operator's system separately sends a "Tracking Export" email
whose subject is `<Client> <Season> <Year> <Type> - Engagement Tracking Report`
and whose body says `…for job <n>…` with the overview PDF attached.

So a send is assembled by **JobID**: all export emails for one send share a
JobID, and the overview-PDF email of the same JobID supplies the identity. This
module groups by JobID, stages every attachment into a JobID-keyed drop folder,
dedups, runs the Phase 1 pipeline (identity from the overview subject), and on a
complete send marks the messages processed and moves the folder to processed/.

The Gmail wire protocol lives behind EmailSource (tested here with a fake).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from . import naming
from .naming import SendIdentity
from .pipeline import SendResult, assess_completeness, process_folder
from .state_io import write_json_atomic

STATE_FILE = ".intake_state.json"

# ExactTarget export-notification body, e.g.
# "… Exported File: export_27241394.csv Exported Type: click Exported for - JobID: 687422 …"
_EXPORT_RE = re.compile(
    r"Exported File:\s*(?P<file>\S+)\s+Exported Type:\s*(?P<type>\w+).*?JobID:\s*(?P<job>\d+)",
    re.IGNORECASE | re.DOTALL,
)
# Overview-PDF email body, e.g. "The PDF file for job 687422 is attached."
_OVERVIEW_JOB_RE = re.compile(r"for job\s*(?P<job>\d+)", re.IGNORECASE)


class Attachment:
    """An email attachment. `data` may be eager (passed in) or lazy (fetched via
    `loader` only when first accessed) — so the puller can skip downloading the
    bytes for sends it won't process (e.g. no overview PDF yet)."""

    def __init__(self, filename: str, data: bytes | None = None, *, loader=None):
        self.filename = filename
        self._data = data
        self._loader = loader

    @property
    def data(self) -> bytes:
        if self._data is None and self._loader is not None:
            self._data = self._loader()
        if self._data is None:
            raise ValueError(f"Attachment {self.filename!r} has no data or loader.")
        return self._data


@dataclass(frozen=True)
class EmailMessage:
    id: str
    subject: str
    attachments: tuple[Attachment, ...]
    body: str = ""


@runtime_checkable
class EmailSource(Protocol):
    """The Gmail seam. The real adapter and the test fake both satisfy this."""

    def fetch_labeled(self, label: str) -> list[EmailMessage]: ...

    def mark_processed(self, message_id: str) -> None: ...


@dataclass
class StagedSend:
    job_id: str
    drop_folder: Path
    message_ids: list[str]
    identity: SendIdentity | None = None
    result: SendResult | None = None     # set when processing succeeded
    pending_reason: str | None = None    # set when left pending
    log: list[str] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        return self.identity.folder_name if self.identity else f"job-{self.job_id}"


def parse_export_email(msg: EmailMessage) -> tuple[str, str] | None:
    """-> (job_id, exported_type) for an ExactTarget export notification, else None."""
    m = _EXPORT_RE.search(msg.body or "")
    return (m.group("job"), m.group("type").lower()) if m else None


def parse_overview_email(msg: EmailMessage) -> tuple[str, SendIdentity] | None:
    """-> (job_id, identity) for the overview-PDF email, else None.

    Identified by the 'for job <n>' body + a PDF attachment (distinctive to the
    'Tracking Export' system email; the operator's older personal emails don't
    have that body). The identity is read FROM THE PDF CONTENT (its 'Name :'
    line), so the operator does NOT need to title the email subject. Falls back
    to a titled subject if the PDF can't be read."""
    jm = _OVERVIEW_JOB_RE.search(msg.body or "")
    if not jm:
        return None
    pdf = next((a for a in msg.attachments if a.filename.lower().endswith(".pdf")), None)
    if pdf is None:
        return None

    identity: SendIdentity | None = None
    from . import overview  # local import to avoid any import cycle
    try:
        name = overview.parse_summary(pdf.data).get("Name")  # 'Client - Season Year Type'
        if name:
            identity = naming.parse_send_identity(str(name))
    except Exception:  # noqa: BLE001 - fall back to the subject below
        identity = None
    if identity is None:
        try:
            identity = naming.parse_overview_subject(msg.subject or "")
        except ValueError:
            return None
    return jm.group("job"), identity


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_state(drop_root: Path) -> dict:
    path = drop_root / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"processed_message_ids": []}


def _save_state(drop_root: Path, state: dict) -> None:
    write_json_atomic(drop_root / STATE_FILE, state)


def _save_dedup(drop_folder: Path, att: Attachment, seen: dict[str, str]) -> bool:
    """Write an attachment unless an identical one (by content hash) is staged.
    Different content under the same name is suffixed, never clobbered."""
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


@dataclass
class _Group:
    msgs: list[EmailMessage] = field(default_factory=list)
    identity: SendIdentity | None = None


def pull_and_stage(
    source: EmailSource,
    label: str,
    drop_root: str | Path,
    *,
    process: Callable[..., SendResult] = process_folder,
    completeness: Callable[[SendResult], tuple[bool, list[str]]] = assess_completeness,
) -> list[StagedSend]:
    """Pull labeled messages, group by JobID, stage, process complete sends, and
    move them to processed/. Idempotent: already-processed message ids are
    skipped, so re-running never double-files, double-marks, or re-processes."""
    drop_root = Path(drop_root)
    inbox = drop_root / "inbox"
    processed_root = drop_root / "processed"
    drop_root.mkdir(parents=True, exist_ok=True)

    state = _load_state(drop_root)
    done: set[str] = set(state["processed_message_ids"])

    # Group fresh messages by JobID; the overview email supplies the identity.
    groups: dict[str, _Group] = {}
    for msg in source.fetch_labeled(label):
        if msg.id in done:
            continue
        ov = parse_overview_email(msg)
        if ov is not None:
            job_id, identity = ov
            g = groups.setdefault(job_id, _Group())
            g.identity = identity
            g.msgs.append(msg)
            continue
        ex = parse_export_email(msg)
        if ex is not None:
            job_id, _ = ex
            groups.setdefault(job_id, _Group()).msgs.append(msg)
        # else: a labeled email that is neither -> ignored (not a report email).

    staged: list[StagedSend] = []
    for job_id, g in groups.items():
        drop_folder = inbox / f"job_{job_id}"
        item = StagedSend(job_id, drop_folder, [m.id for m in g.msgs], identity=g.identity)

        # No overview email yet -> we don't know the send's identity. Skip WITHOUT
        # downloading any attachments (keeps frequent runs cheap); it'll be picked
        # up on a later run once the overview-PDF email arrives.
        if g.identity is None:
            item.pending_reason = "awaiting overview-PDF email (identity) for this JobID"
            item.log.append(f"PENDING: {item.pending_reason} (not downloaded)")
            staged.append(item)
            continue

        # Identity present -> stage (this is where attachment bytes are fetched).
        seen: dict[str, str] = {}
        if drop_folder.exists():
            for existing in drop_folder.glob("*"):
                if existing.is_file():
                    seen[_sha(existing.read_bytes())] = existing.name
        for msg in g.msgs:
            for att in msg.attachments:
                if _save_dedup(drop_folder, att, seen):
                    item.log.append(f"staged {att.filename} (from {msg.id})")

        try:
            item.result = process(drop_folder, g.identity)
        except Exception as exc:  # noqa: BLE001 - pending is an expected outcome
            item.pending_reason = f"processing error: {exc}"
            item.log.append(f"PENDING ({item.pending_reason})")
            staged.append(item)
            continue

        ok, missing = completeness(item.result)
        if not ok:
            item.pending_reason = f"incomplete: missing {missing}"
            item.log.append(f"PENDING (not yet complete): missing {missing}")
            staged.append(item)
            continue

        for msg in g.msgs:
            source.mark_processed(msg.id)
            done.add(msg.id)
        dest = processed_root / g.identity.folder_name
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

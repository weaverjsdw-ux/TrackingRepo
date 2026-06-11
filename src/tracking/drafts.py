"""Build and create Gmail drafts for engagement report delivery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import naming, run_state
from .contacts import Contact
from .naming import SendIdentity


class DraftError(ValueError):
    """Raised when a report draft would be incomplete or unsafe."""


@dataclass(frozen=True)
class DraftEmail:
    to: list[str]
    subject: str
    body: str
    attachments: list[Path]


@dataclass(frozen=True)
class DraftOutcome:
    draft_id: str
    created: bool


@runtime_checkable
class DraftWriter(Protocol):
    def create_draft(self, draft: DraftEmail) -> str: ...


def build_engagement_draft(
    identity: SendIdentity,
    report_dir: str | Path,
    contact: Contact,
) -> DraftEmail:
    folder = Path(report_dir)
    if not folder.is_dir():
        raise DraftError(f"Report folder not found: {folder}")

    official_pdf = folder / naming.finished_pdf_name(identity)
    if not official_pdf.is_file():
        raise DraftError(f"Missing official overview PDF: {official_pdf.name}")

    attachments = sorted(
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".csv", ".pdf"}
        and p.name.startswith(f"{identity.prefix} - ")
        and "lead scoring" not in p.name.lower()
        and not p.name.lower().startswith("sd_")
    )
    if official_pdf not in attachments:
        raise DraftError(f"Official overview PDF is not attached: {official_pdf.name}")

    body = (
        "Hi,\n\n"
        "Attached are the engagement tracking reports.\n\n"
        "Best,\n"
    )
    return DraftEmail(
        to=[contact.pc_email],
        subject=naming.email_subject(identity),
        body=body,
        attachments=attachments,
    )


def create_engagement_draft(
    writer: DraftWriter,
    state_path: str | Path,
    identity: SendIdentity,
    report_dir: str | Path,
    contact: Contact,
) -> DraftOutcome:
    send_key = identity.folder_name
    existing = run_state.draft_id_for(state_path, send_key)
    if existing:
        return DraftOutcome(draft_id=existing, created=False)

    draft = build_engagement_draft(identity, report_dir, contact)
    draft_id = writer.create_draft(draft)
    run_state.remember_draft(state_path, send_key, draft_id)
    return DraftOutcome(draft_id=draft_id, created=True)

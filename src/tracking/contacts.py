"""Operator-maintained contact lookup for engagement report delivery."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .naming import SendIdentity

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactError(ValueError):
    """Raised when report delivery contact data is missing or unsafe."""


@dataclass(frozen=True)
class Contact:
    client: str
    pc_email: str
    report_delivery_enabled: bool


def _client_key(s: str) -> str:
    return " ".join(sorted(re.findall(r"[a-z0-9]+", str(s).lower())))


def _bool(value: str) -> bool:
    v = str(value).strip().lower()
    if v in {"yes", "y", "true", "1", "enabled"}:
        return True
    if v in {"no", "n", "false", "0", "disabled"}:
        return False
    raise ContactError(f"Invalid report_delivery_enabled value {value!r}; use yes/no.")


def load_contacts(path: str | Path) -> list[Contact]:
    p = Path(path)
    if not p.exists():
        raise ContactError(f"Contact file not found: {p}")

    with p.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"client", "pc_email", "report_delivery_enabled"}
        missing = required - {h.strip() for h in (reader.fieldnames or [])}
        if missing:
            raise ContactError(f"Contact file {p} missing columns: {sorted(missing)}")

        contacts: list[Contact] = []
        for row_num, row in enumerate(reader, start=2):
            normalized = {str(k).strip(): v for k, v in row.items()}
            client = (normalized.get("client") or "").strip()
            email = (normalized.get("pc_email") or "").strip()
            if not client:
                raise ContactError(f"Row {row_num}: client is required.")
            if not _EMAIL_RE.match(email):
                raise ContactError(f"Row {row_num}: Invalid PC email {email!r}.")
            contacts.append(Contact(
                client=client,
                pc_email=email,
                report_delivery_enabled=_bool(normalized.get("report_delivery_enabled", "")),
            ))
    return contacts


def report_contact_for(contacts: list[Contact], identity: SendIdentity) -> Contact:
    matches = [c for c in contacts if _client_key(c.client) == _client_key(identity.client)]
    if not matches:
        raise ContactError(f"No contact for client {identity.client!r}.")
    if len(matches) > 1:
        raise ContactError(f"Multiple contacts for client {identity.client!r}; refusing to guess.")
    contact = matches[0]
    if not contact.report_delivery_enabled:
        raise ContactError(f"Report delivery is disabled for client {identity.client!r}.")
    return contact

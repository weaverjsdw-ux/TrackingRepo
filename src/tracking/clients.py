"""clients.csv — the one source of truth for HIPAA status + PC contacts (BRIEF §5).

Real file (clients.csv) is git-ignored; clients.example.csv is the committed
template. Lines starting with '#' are comments. Lookups fail loud on an unknown
client so the HIPAA branch is never guessed (BRIEF §3, §2.D).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


class ClientNotFound(KeyError):
    """Raised when a client is not present in clients.csv (HIPAA status unknown)."""


@dataclass(frozen=True)
class ClientInfo:
    client: str
    hipaa: bool | None  # True=yes, False=no, None="?" (needs operator review)
    pc_name: str
    pc_email: str


# Accepted values for the hipaa column. "?" (and synonyms) => None = review.
_HIPAA_VALUES = {
    "yes": True, "y": True,
    "no": False, "n": False,
    "?": None, "unknown": None, "review": None, "tbd": None,
}


def _norm(s: str) -> str:
    return str(s).strip().lower()


def load_clients(path: str | Path) -> dict[str, ClientInfo]:
    """Load clients.csv -> {normalized client name -> ClientInfo}."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"clients.csv not found at {path} (copy clients.example.csv).")
    out: dict[str, ClientInfo] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(r for r in fh if not r.lstrip().startswith("#"))
        for row in reader:
            client = (row.get("client") or "").strip()
            if not client:
                continue
            hipaa_raw = _norm(row.get("hipaa", ""))
            if hipaa_raw not in _HIPAA_VALUES:
                raise ValueError(
                    f"clients.csv: client {client!r} has hipaa={row.get('hipaa')!r} "
                    f"(must be 'yes', 'no', or '?')."
                )
            out[_norm(client)] = ClientInfo(
                client=client,
                hipaa=_HIPAA_VALUES[hipaa_raw],
                pc_name=(row.get("pc_name") or "").strip(),
                pc_email=(row.get("pc_email") or "").strip(),
            )
    return out


def lookup(clients: dict[str, ClientInfo], client: str) -> ClientInfo:
    """Return the ClientInfo for a client, or raise ClientNotFound (never guess)."""
    info = clients.get(_norm(client))
    if info is None:
        raise ClientNotFound(
            f"Client {client!r} is not in clients.csv — cannot determine HIPAA "
            f"status. Add a row before sending its lead-scoring notification."
        )
    if info.hipaa and not info.pc_email:
        raise ValueError(
            f"Client {client!r} is flagged HIPAA but has no pc_email in clients.csv."
        )
    return info

"""Source-of-truth naming + send-identity logic.

Every output filename and email subject in the pipeline is built here and
nowhere else (the brief's "one source-of-truth function" rule, BRIEF §1, §2,
§3 "Deterministic naming"). The lead-scoring file is the sole exception: it is
never renamed (BRIEF §2.B) and so never passes through this module.

Convention (observed from the golden FINISHED files, not the stale procedure
prose -- BRIEF §1.1):

    "Client Name Season Year Type - <Description>.csv"

Note the separator is " - " (space-hyphen-space) and it appears *only* before
the description. The source folder name uses " - " between the client and the
season block ("Bradley University - Spring 2026 eNL"); in the output that
collapses to a single space.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The metric descriptions double as (a) the " - <Description>" filename suffix
# and (b) the Sheet column-header text we match on later (BRIEF §2.A §11).
# Keep this list as the canonical spelling.
METRIC_DESCRIPTIONS = (
    "Total Sent",
    "Unique Opens",
    "Unique Clicks",
    "Hard Bounces",
    "Soft Bounces",
    "Block Bounces",
    "Unsubscribes",
)

# The overview PDF has a fixed description (BRIEF §2.A §5).
OVERVIEW_DESCRIPTION = "Engagement Tracking Report"

# The request (booklet) file's description (operator decision). The words
# "Request Your" are NOT present in the export data -- they come from the email's
# CTA text -- so this is a fixed label, not data-derived.
# FLAG: this label may vary by client; when that surfaces, move it to a
# per-client override (e.g. clients.csv) rather than hard-coding per client.
REQUEST_FILE_DESCRIPTION = "Request Your"

_SEP = " - "
_YEAR_RE = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class SendIdentity:
    """The four fields that identify a send, read from the folder name or the
    Gmail subject (BRIEF §2.A §1, Q5). No job sheet is needed for these."""

    client: str
    season: str
    year: str
    type: str  # "eNL" or "ePC"

    @property
    def prefix(self) -> str:
        """The 'Client Name Season Year Type' stem shared by every output."""
        return f"{self.client} {self.season} {self.year} {self.type}"

    @property
    def folder_name(self) -> str:
        """The canonical drop-folder name 'Client - Season Year Type', which
        parse_send_identity() round-trips back to this identity."""
        return f"{self.client}{_SEP}{self.season} {self.year} {self.type}"


def parse_send_identity(folder_name: str) -> SendIdentity:
    """Parse 'Bradley University - Spring 2026 eNL' -> SendIdentity.

    Splits on the first ' - ': everything before is the (possibly multi-word)
    client name; the remainder is 'Season Year Type'. Year is the 4-digit
    token; the season is everything before it, the type everything after.
    Raises loudly on anything that does not fit (BRIEF §3 "Loud failure").
    """
    name = folder_name.strip()
    if _SEP not in name:
        raise ValueError(
            f"Cannot parse send identity: folder name {name!r} has no ' - ' "
            f"separating client from 'Season Year Type'."
        )
    client, _, tail = name.partition(_SEP)
    client = client.strip()
    tokens = tail.split()
    year_idx = next((i for i, t in enumerate(tokens) if _YEAR_RE.match(t)), None)
    if year_idx is None:
        raise ValueError(
            f"Cannot parse send identity: no 4-digit year found in {tail!r} "
            f"(from folder {name!r})."
        )
    season = " ".join(tokens[:year_idx]).strip()
    year = tokens[year_idx]
    type_ = " ".join(tokens[year_idx + 1 :]).strip()
    if not client or not season or not type_:
        raise ValueError(
            f"Cannot parse send identity from {name!r}: "
            f"client={client!r} season={season!r} year={year!r} type={type_!r}."
        )
    return SendIdentity(client=client, season=season, year=year, type=type_)


def parse_send_identity_from_prefix(prefix: str) -> SendIdentity:
    """Parse the space-joined prefix form 'Bradley University Spring 2026 eNL'
    (no ' - ' separator) -> SendIdentity.

    Used for the overview-PDF email subject, which the operator types as
    '<Client> <Season> <Year> <Type> - Engagement Tracking Report'. The 4-digit
    token is the year; the single token before it is the season; everything
    before the season is the (multi-word) client; everything after the year is
    the type (eNL/ePC/eQC/...). Raises loudly on anything that does not fit.
    """
    tokens = prefix.split()
    year_idx = next((i for i, t in enumerate(tokens) if _YEAR_RE.match(t)), None)
    if year_idx is None or year_idx < 1 or year_idx + 1 >= len(tokens):
        raise ValueError(
            f"Cannot parse send identity from prefix {prefix!r}: expected "
            f"'<Client> <Season> <Year> <Type>'."
        )
    client = " ".join(tokens[: year_idx - 1]).strip()
    season = tokens[year_idx - 1]
    year = tokens[year_idx]
    type_ = " ".join(tokens[year_idx + 1 :]).strip()
    if not client or not season or not type_:
        raise ValueError(
            f"Cannot parse send identity from prefix {prefix!r}: "
            f"client={client!r} season={season!r} year={year!r} type={type_!r}."
        )
    return SendIdentity(client=client, season=season, year=year, type=type_)


def parse_overview_subject(subject: str) -> SendIdentity:
    """Parse the overview-PDF email subject
    '<Client> <Season> <Year> <Type> - Engagement Tracking Report' -> identity.
    Raises loudly if the subject is not in that exact form."""
    s = subject.strip()
    suffix = f"{_SEP}{OVERVIEW_DESCRIPTION}"
    if not s.lower().endswith(suffix.lower()):
        raise ValueError(
            f"Subject {subject!r} is not an overview-PDF subject "
            f"(must end with '{suffix}')."
        )
    return parse_send_identity_from_prefix(s[: len(s) - len(suffix)].strip())


def finished_csv_name(identity: SendIdentity, description: str) -> str:
    """'<prefix> - <Description>.csv'. The one true CSV namer."""
    description = description.strip()
    if not description:
        raise ValueError("Refusing to build a filename with an empty description.")
    return f"{identity.prefix}{_SEP}{description}.csv"


def finished_pdf_name(identity: SendIdentity) -> str:
    """'<prefix> - Engagement Tracking Report.pdf' (BRIEF §2.A §5)."""
    return f"{identity.prefix}{_SEP}{OVERVIEW_DESCRIPTION}.pdf"


def email_subject(identity: SendIdentity) -> str:
    """'Engagement Tracking — <prefix>' (BRIEF §2.A §10). Em dash is intentional."""
    return f"Engagement Tracking — {identity.prefix}"

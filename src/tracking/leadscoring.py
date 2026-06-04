"""Phase 4 — lead-scoring intake + HIPAA routing decision (BRIEF §2.B/§2.D).

Lead-scoring reports arrive as their own email (operator decision): subject
'Lead Scoring - <Client> <Season> <Year> <Type>' with an 'sd_<Client> - Lead
Scoring<date>.csv' attachment. The tool pulls the labeled email, reads the
client, looks up clients.csv, and decides the route:

  * non-HIPAA -> notify kathryn.baugh@pentera.com that the lead score is ready
    to upload (exact email content TBD by operator);
  * HIPAA     -> template-merge + email the report to the project coordinator
    (DEFERRED this round);
  * client not in clients.csv -> fail loud (never guess HIPAA status).

This module parses the email and decides the route; composing/sending the actual
email is a separate step (behind an EmailSender, fake-tested, no real sends).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import clients, naming
from .clients import ClientInfo
from .intake import Attachment, EmailMessage

KATHRYN_EMAIL = "kathryn.baugh@pentera.com"

_SUBJECT_RE = re.compile(r"(?i)^\s*lead\s+scoring\s*-\s*(?P<prefix>.+?)\s*$")


@dataclass(frozen=True)
class LeadScoring:
    identity: naming.SendIdentity
    sd_attachment: Attachment


@dataclass(frozen=True)
class Route:
    kind: str  # "notify-kathryn" | "hipaa"
    lead: LeadScoring
    client_info: ClientInfo


def parse_lead_scoring_email(msg: EmailMessage) -> LeadScoring | None:
    """-> LeadScoring for a 'Lead Scoring - <...>' email with an sd_ attachment,
    else None."""
    m = _SUBJECT_RE.match(msg.subject or "")
    if not m:
        return None
    sd = next(
        (a for a in msg.attachments
         if a.filename.lower().startswith("sd_") and a.filename.lower().endswith(".csv")),
        None,
    )
    if sd is None:
        return None
    try:
        identity = naming.parse_send_identity_from_prefix(m.group("prefix"))
    except ValueError:
        return None
    return LeadScoring(identity=identity, sd_attachment=sd)


def route(lead: LeadScoring, clients_map: dict[str, ClientInfo]) -> Route:
    """Decide where a lead-scoring report goes. Raises clients.ClientNotFound if
    the client is absent from clients.csv (never guesses HIPAA status)."""
    info = clients.lookup(clients_map, lead.identity.client)
    return Route(kind=("hipaa" if info.hipaa else "notify-kathryn"), lead=lead, client_info=info)

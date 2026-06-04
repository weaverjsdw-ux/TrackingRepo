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
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from . import clients, naming
from .clients import ClientInfo
from .intake import Attachment, EmailMessage, EmailSource

KATHRYN_EMAIL = "kathryn.baugh@pentera.com"

_SUBJECT_RE = re.compile(r"(?i)^\s*lead\s+scoring\s*-\s*(?P<prefix>.+?)\s*$")


@dataclass(frozen=True)
class LeadScoring:
    identity: naming.SendIdentity
    sd_attachment: Attachment


@dataclass(frozen=True)
class Route:
    kind: str  # "notify-kathryn" | "hipaa" | "review"
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
    if info.hipaa is None:
        kind = "review"            # marked '?' in clients.csv -> confirm before sending
    elif info.hipaa:
        kind = "hipaa"            # deferred branch
    else:
        kind = "notify-kathryn"
    return Route(kind=kind, lead=lead, client_info=info)


@runtime_checkable
class EmailSender(Protocol):
    def send(self, to, subject, body, attachments=(), cc=None) -> str: ...


@dataclass
class Email:
    to: str
    subject: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    cc: str | None = None


def compose_kathryn_email(route: Route) -> Email:
    """The non-HIPAA notification (operator-specified): To Kathryn, subject
    'Lead Scoring - <prefix>', no body, sd_ file attached."""
    return Email(
        to=KATHRYN_EMAIL,
        subject=f"Lead Scoring - {route.lead.identity.prefix}",
        body="",
        attachments=[route.lead.sd_attachment],
    )


@dataclass
class LeadResult:
    subject: str
    identity: naming.SendIdentity | None = None
    kind: str | None = None          # "notify-kathryn" | "hipaa"
    sent: bool = False
    message_id: str | None = None
    skipped_reason: str | None = None
    log: list[str] = field(default_factory=list)


def run(
    source: EmailSource,
    label: str,
    sender: EmailSender,
    clients_map: dict[str, ClientInfo],
    *,
    commit: bool = False,
) -> list[LeadResult]:
    """Pull labeled lead-scoring emails, route each, and (when commit=True) send
    the non-HIPAA notification to Kathryn + mark the email processed.

    HIPAA clients are left for the deferred branch (not sent, label kept). An
    unknown client (not in clients.csv) is left untouched and flagged. Nothing is
    sent unless commit=True."""
    results: list[LeadResult] = []
    for msg in source.fetch_labeled(label):
        lead = parse_lead_scoring_email(msg)
        if lead is None:
            continue  # not a lead-scoring report email
        res = LeadResult(subject=msg.subject, identity=lead.identity)
        try:
            r = route(lead, clients_map)
        except clients.ClientNotFound as exc:
            res.skipped_reason = str(exc)
            res.log.append(f"SKIP (unknown client): {exc}")
            results.append(res)
            continue
        res.kind = r.kind
        if r.kind == "hipaa":
            res.skipped_reason = "HIPAA branch deferred (not built this round)"
            res.log.append("SKIP: " + res.skipped_reason)
            results.append(res)
            continue
        if r.kind == "review":
            res.skipped_reason = "HIPAA status is '?' in clients.csv — confirm yes/no before sending"
            res.log.append("SKIP: " + res.skipped_reason)
            results.append(res)
            continue
        email = compose_kathryn_email(r)
        res.log.append(f"would send -> {email.to} | subj={email.subject!r} | "
                       f"attach={[a.filename for a in email.attachments]}")
        if commit:
            res.message_id = sender.send(email.to, email.subject, email.body,
                                         email.attachments, email.cc)
            source.mark_processed(msg.id)
            res.sent = True
            res.log.append(f"SENT (message {res.message_id}); label removed")
        results.append(res)
    return results

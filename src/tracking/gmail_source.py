"""Real Gmail adapter implementing intake.EmailSource (BRIEF §1).

Auth mode (operator decision): OAuth user-consent (installed-app flow) — works
for a personal or Workspace mailbox with no admin involvement; a one-time
browser consent stores a refresh token at GOOGLE_TOKEN_PATH. To switch to a
Workspace service account later, swap `_build_service()` for a delegated
service-account credential; nothing else in the pipeline changes.

The google client libraries are an OPTIONAL extra (`pip install -e .[gmail]`) and
are imported lazily, so the rest of the package (and the whole test suite) loads
and runs without them. This adapter is exercised live against a real inbox; the
credential-free orchestration is covered by tests/test_intake.py via a fake.

Mark-processed (operator decision): remove the intake label from the message.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from email.message import EmailMessage as MimeMessage
from pathlib import Path

from .intake import Attachment, EmailMessage
from .drafts import DraftEmail

# modify = read + remove labels (mark-processed); compose = create drafts only.
# This adapter never sends email. Older tokens may need reauthorization to add
# the compose scope.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailSource:
    """An intake.EmailSource backed by the Gmail API."""

    def __init__(
        self,
        client_secret: str | None = None,
        token_path: str | None = None,
    ):
        self._client_secret = client_secret or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        self._token_path = token_path or os.environ.get("GOOGLE_TOKEN_PATH", "secrets/token.json")
        self._service = None
        self._label_cache: dict[str, str] = {}

    # --- EmailSource interface -------------------------------------------------

    # Find report emails BY SENDER directly (no operator Gmail filter needed):
    # ExactTarget export notifications + the operator's overview-PDF emails.
    REPORT_QUERY = (
        '(from:help@exacttarget.com OR '
        '(from:noreply@memberemail.com subject:"Engagement Tracking Report")) '
        'newer_than:45d'
    )

    def fetch_labeled(self, label: str) -> list[EmailMessage]:
        """Return report emails located BY SENDER (not by label), so it works
        whether or not a Gmail filter labels them. Idempotency is handled by the
        intake state file (already-processed message ids are skipped), and
        mark_processed removes the label as a 'done' visual. We deliberately do
        NOT filter by label here: a filter that labels incoming exports must not
        cause them to be skipped."""
        service = self._svc()
        q = self.REPORT_QUERY
        out: list[EmailMessage] = []
        req = service.users().messages().list(userId="me", q=q)
        while req is not None:
            resp = req.execute()
            for ref in resp.get("messages", []):
                out.append(self._load_message(ref["id"]))
            req = service.users().messages().list_next(req, resp)
        return out

    def mark_processed(self, message_id: str) -> None:
        # Remove the label as a 'done' visual (labeled = still queued, unlabeled =
        # handled). Idempotency is the state file, not the label. No-op if absent.
        label = os.environ.get("GMAIL_LABEL", "tracking-reports")
        self._svc().users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": [self._label_id(label)]},
        ).execute()

    def create_draft(self, draft: DraftEmail) -> str:
        """Create a Gmail draft and return its draft id. This never sends."""
        resp = self._svc().users().drafts().create(
            userId="me",
            body={"message": {"raw": _build_draft_message(draft)}},
        ).execute()
        return resp["id"]

    # --- internals -------------------------------------------------------------

    def _svc(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Gmail support requires the optional extra: pip install -e .[gmail]"
            ) from exc

        token = Path(self._token_path)
        creds = None
        if token.exists():
            creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self._client_secret:
                    raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET is not set.")
                flow = InstalledAppFlow.from_client_secrets_file(self._client_secret, SCOPES)
                creds = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    authorization_prompt_message=(
                        "\nA browser window is opening for Google sign-in.\n"
                        "Sign in as the report inbox, click Allow, then return here.\n"
                        "Do NOT close this window or press Ctrl-C until it says Authorized.\n"
                    ),
                    success_message=(
                        "Authorized — you can close this browser tab and return to the terminal."
                    ),
                )
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text(creds.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _label_id(self, label: str) -> str:
        if not self._label_cache:
            resp = self._svc().users().labels().list(userId="me").execute()
            self._label_cache = {l["name"]: l["id"] for l in resp.get("labels", [])}
        if label not in self._label_cache:
            raise RuntimeError(f"Gmail label {label!r} not found on this account.")
        return self._label_cache[label]

    def _load_message(self, message_id: str) -> EmailMessage:
        service = self._svc()
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("subject", "")
        attachments = tuple(self._extract_attachments(message_id, msg["payload"]))
        body = self._extract_text(msg["payload"]) or msg.get("snippet", "")
        return EmailMessage(id=message_id, subject=subject, attachments=attachments, body=body)

    def _extract_text(self, payload: dict) -> str:
        """Concatenate text/plain parts (for parsing Exported Type / JobID)."""
        chunks: list[str] = []
        for part in _walk_parts(payload):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data")
                if data:
                    chunks.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
        return "\n".join(chunks)

    def _extract_attachments(self, message_id: str, payload: dict) -> list[Attachment]:
        """Return attachments with LAZY data: large parts (attachmentId) are only
        downloaded when their .data is accessed (i.e. when actually staged), so a
        pull doesn't fetch bytes for sends it won't process."""
        out: list[Attachment] = []
        for part in _walk_parts(payload):
            filename = part.get("filename")
            body = part.get("body", {})
            if not filename:
                continue
            if "attachmentId" in body:
                def loader(mid=message_id, aid=body["attachmentId"]) -> bytes:
                    att = self._svc().users().messages().attachments().get(
                        userId="me", messageId=mid, id=aid).execute()
                    return base64.urlsafe_b64decode(att.get("data", ""))
                out.append(Attachment(filename, loader=loader))
            elif body.get("data"):  # small inline part — already present, free
                out.append(Attachment(filename, base64.urlsafe_b64decode(body["data"])))
        return out


def _walk_parts(payload: dict):
    """Yield every MIME part (depth-first), including nested multiparts."""
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        stack.extend(part.get("parts", []))


def _build_draft_message(draft: DraftEmail) -> str:
    """Build Gmail's base64url raw MIME message payload."""
    msg = MimeMessage()
    msg["To"] = ", ".join(draft.to)
    msg["Subject"] = draft.subject
    msg.set_content(draft.body)

    for path in draft.attachments:
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

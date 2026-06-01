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
import os
from pathlib import Path

from .intake import Attachment, EmailMessage

# Read-only would be enough to fetch, but removing the label on mark-processed
# needs modify scope.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


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

    def fetch_labeled(self, label: str) -> list[EmailMessage]:
        service = self._svc()
        label_id = self._label_id(label)
        out: list[EmailMessage] = []
        req = service.users().messages().list(userId="me", labelIds=[label_id])
        while req is not None:
            resp = req.execute()
            for ref in resp.get("messages", []):
                out.append(self._load_message(ref["id"]))
            req = service.users().messages().list_next(req, resp)
        return out

    def mark_processed(self, message_id: str) -> None:
        # Operator decision: remove the intake label (leave the email otherwise).
        label = os.environ.get("GMAIL_LABEL", "tracking-reports")
        self._svc().users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": [self._label_id(label)]},
        ).execute()

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
                creds = flow.run_local_server(port=0)
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
        return EmailMessage(id=message_id, subject=subject, attachments=attachments)

    def _extract_attachments(self, message_id: str, payload: dict) -> list[Attachment]:
        out: list[Attachment] = []
        for part in _walk_parts(payload):
            filename = part.get("filename")
            body = part.get("body", {})
            if not filename:
                continue
            if "attachmentId" in body:
                att = self._svc().users().messages().attachments().get(
                    userId="me", messageId=message_id, id=body["attachmentId"]
                ).execute()
                data = att.get("data", "")
            else:
                data = body.get("data", "")
            if data:
                out.append(Attachment(filename, base64.urlsafe_b64decode(data)))
        return out


def _walk_parts(payload: dict):
    """Yield every MIME part (depth-first), including nested multiparts."""
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        stack.extend(part.get("parts", []))

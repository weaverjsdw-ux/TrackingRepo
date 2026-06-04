"""Live Google Sheets adapter implementing sheet.SheetWriter (BRIEF §2.A §11).

Access via a service account (operator adds the ...iam.gserviceaccount.com
address as Editor on the 2026 Print Status Report; for the trial the coder uses
a copy). The google client libraries are an OPTIONAL extra
(`pip install -e .[sheets]`) and imported lazily, so the package and the whole
test suite load without them.

This adapter is exercised against a real sheet copy; the matching / cross-check
logic is fully covered offline by tests/test_sheet.py via a FakeSheet. Marked
structural-only until run against a live copy.
"""

from __future__ import annotations

import os


def _col_to_a1(col: int) -> str:
    """0-indexed column -> A1 letters (0->A, 26->AA)."""
    s = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        s = chr(65 + rem) + s
    return s


class GoogleSheetsWriter:
    """A sheet.SheetWriter backed by the Google Sheets API v4."""

    def __init__(
        self,
        spreadsheet_id: str | None = None,
        tab: str | None = None,
        service_account: str | None = None,
    ):
        self._spreadsheet_id = spreadsheet_id or os.environ.get("SHEET_ID")
        self._tab = tab or os.environ.get("SHEET_TAB", "Sheet1")
        self._sa = service_account or os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT")
        self._svc = None

    def _service(self):
        if self._svc is not None:
            return self._svc
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Sheets support requires the optional extra: pip install -e .[sheets]"
            ) from exc
        if not self._sa:
            raise RuntimeError("GOOGLE_SHEETS_SERVICE_ACCOUNT is not set.")
        creds = Credentials.from_service_account_file(
            self._sa, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self._svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._svc

    # --- SheetWriter interface -------------------------------------------------

    def get_values(self) -> list[list[str]]:
        resp = (
            self._service().spreadsheets().values()
            .get(spreadsheetId=self._spreadsheet_id, range=self._tab,
                 valueRenderOption="UNFORMATTED_VALUE")
            .execute()
        )
        return resp.get("values", [])

    def update_cell(self, row: int, col: int, value) -> None:
        a1 = f"{self._tab}!{_col_to_a1(col)}{row + 1}"
        self._service().spreadsheets().values().update(
            spreadsheetId=self._spreadsheet_id,
            range=a1,
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()

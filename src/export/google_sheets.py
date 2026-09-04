"""Google Sheets export. Requires one-time setup (see
docs/GOOGLE_SHEETS_SETUP.md): a GCP service account with Sheets+Drive API
access, its JSON key at GOOGLE_SERVICE_ACCOUNT_FILE, and that service
account's email added as an Editor on the target spreadsheet (or leave
GOOGLE_SHEET_ID blank to have this script create a new spreadsheet and
print its URL).

Without credentials this raises SheetsNotConfigured — callers (scripts/
export_to_sheets.py) catch that and fall back to "here are your CSV/XLSX
files, upload them manually" rather than crashing the whole run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.export.tabular import TAB_HEADERS
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class SheetsNotConfigured(Exception):
    pass


def _stringify_row(row: dict[str, Any], headers: list[str]) -> list[str]:
    out = []
    for h in headers:
        v = row.get(h, "")
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        out.append("" if v is None else str(v))
    return out


def push_to_google_sheets(
    tabs: dict[str, list[dict[str, Any]]],
    service_account_file: str,
    sheet_id: str = "",
    sheet_title: str = "FrontierAtlas Intelligence Pipeline Output",
) -> str:
    """Returns the spreadsheet URL on success. Raises SheetsNotConfigured if
    the service-account file is missing (i.e. the one-time credential step
    documented in docs/GOOGLE_SHEETS_SETUP.md hasn't been done yet).
    """
    if not Path(service_account_file).is_file():
        raise SheetsNotConfigured(f"No service account file at {service_account_file}. See docs/GOOGLE_SHEETS_SETUP.md.")

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]
    creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
    client = gspread.authorize(creds)

    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    else:
        spreadsheet = client.create(sheet_title)
        spreadsheet.share(None, perm_type="anyone", role="reader")
        logger.info("created_new_spreadsheet", url=spreadsheet.url)

    existing_titles = {ws.title for ws in spreadsheet.worksheets()}

    for tab_name, headers in TAB_HEADERS.items():
        rows = tabs.get(tab_name, [])
        if tab_name in existing_titles:
            ws = spreadsheet.worksheet(tab_name)
            ws.clear()
        else:
            ws = spreadsheet.add_worksheet(title=tab_name, rows=max(len(rows) + 10, 100), cols=max(len(headers) + 2, 10))

        values = [headers] + [_stringify_row(r, headers) for r in rows]
        ws.update(values, "A1")
        ws.freeze(rows=1)
        ws.format(f"A1:{chr(64 + len(headers))}1", {"textFormat": {"bold": True}})

    default_sheet = spreadsheet.worksheets()[0]
    if default_sheet.title == "Sheet1" and default_sheet.title not in TAB_HEADERS:
        try:
            spreadsheet.del_worksheet(default_sheet)
        except Exception:  # noqa: BLE001 - cosmetic cleanup only, never fatal
            pass

    return spreadsheet.url

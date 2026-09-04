"""Google Sheets export. Two supported credential paths, tried in order:

1. A GCP service account JSON key at GOOGLE_SERVICE_ACCOUNT_FILE (see
   docs/GOOGLE_SHEETS_SETUP.md) — its email needs Editor access on the
   target spreadsheet, or leave GOOGLE_SHEET_ID blank to have this script
   create a new spreadsheet under the service account itself.
2. Application Default Credentials (ADC) — e.g. from
   `gcloud auth application-default login --scopes=...` run once against
   the user's own Google account. No JSON key file needed; the resulting
   spreadsheet is created directly in that Google account's own Drive.

Without either, this raises SheetsNotConfigured — callers (scripts/
export_data.py) catch that and fall back to "here are your CSV/XLSX files,
upload them manually" rather than crashing the whole run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.export.tabular import TAB_HEADERS
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
# drive.file (rather than the full drive scope) looks like the more
# privacy-conscious default, but it only covers files the credential's own
# app created or opened via a picker. A file the owner shared afterwards
# with the service account's email -- the only path available to a service
# account with no Drive storage quota of its own -- still lets drive.file
# read/write the sheet's contents, but Drive silently 404s any attempt to
# modify that file's permissions (e.g. making it link-public) under that
# scope. Verified directly against the Drive API: the identical
# permissions.create call that 404s under drive.file returns 200 under
# plain drive. Needed for `spreadsheet.share(...)` below to actually work
# on a shared-not-created file, which is the only case this project uses.


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


def _resolve_credentials(service_account_file: str):
    """Returns (credentials, method) or (None, None) if nothing usable is
    configured. Never raises for a missing/invalid ADC file — that's an
    expected "not set up" state, not an error.
    """
    if Path(service_account_file).is_file():
        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_file(service_account_file, scopes=_SCOPES), "service_account"

    try:
        import google.auth

        creds, _project = google.auth.default(scopes=_SCOPES)
        return creds, "application_default"
    except Exception as e:  # noqa: BLE001 - google.auth raises its own DefaultCredentialsError type
        logger.info("adc_not_available", error=str(e))
        return None, None


def push_to_google_sheets(
    tabs: dict[str, list[dict[str, Any]]],
    service_account_file: str,
    sheet_id: str = "",
    sheet_title: str = "FrontierAtlas Intelligence Pipeline Output",
) -> tuple[str, bool]:
    """Returns (spreadsheet_url, made_public_by_this_call). Raises
    SheetsNotConfigured if neither a service-account file nor Application
    Default Credentials are available — i.e. neither one-time credential
    step documented in docs/GOOGLE_SHEETS_SETUP.md has been done yet.

    made_public_by_this_call is False (not a raised error) when the sharing
    API call itself is rejected -- this happens when GOOGLE_SHEET_ID points
    at a sheet whose owner has restricted editors from changing sharing
    settings. That's a data point for the caller to surface, not a reason
    to fail a run that otherwise successfully wrote every tab.
    """
    creds, method = _resolve_credentials(service_account_file)
    if creds is None:
        raise SheetsNotConfigured(
            f"No service account file at {service_account_file} and no Application Default Credentials "
            "available (`gcloud auth application-default login`). See docs/GOOGLE_SHEETS_SETUP.md."
        )
    logger.info("google_sheets_auth_method", method=method)

    import gspread

    client = gspread.authorize(creds)

    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    else:
        spreadsheet = client.create(sheet_title)
        logger.info("created_new_spreadsheet", url=spreadsheet.url)

    # Required regardless of path: an existing sheet opened via GOOGLE_SHEET_ID
    # is whatever privacy level its human owner left it at, and the assignment
    # requires a publicly viewable link either way. Not fatal if rejected --
    # some file owners restrict editors (including this service account) from
    # changing sharing settings on a file they don't own; the data push below
    # is the primary deliverable and must still complete either way.
    made_public = False
    try:
        spreadsheet.share(None, perm_type="anyone", role="reader")
        made_public = True
    except Exception as e:  # noqa: BLE001 - gspread raises its own APIError type
        logger.info("google_sheets_share_rejected", error=str(e))

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

    return spreadsheet.url, made_public

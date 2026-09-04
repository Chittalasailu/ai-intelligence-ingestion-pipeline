"""Regenerate CSV/XLSX (and optionally Google Sheets) exports from the
current database contents, without re-running any crawler.

Usage:
  python -m scripts.export_data
  python -m scripts.export_data --sheets

The --sheets flag requires the one-time Google service-account setup
documented in docs/GOOGLE_SHEETS_SETUP.md. Without it, this always writes
CSV (one per tab, under data/<vertical>/) and one combined .xlsx workbook —
those are what you'd import into Sheets manually if credentials aren't set
up yet.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.export.google_sheets import SheetsNotConfigured, push_to_google_sheets
from src.export.tabular import TAB_HEADERS, write_csv, write_workbook
from src.storage.db import create_engine, make_session_factory
from src.storage.repository import get_all_tabs
from src.utils.config import ROOT, load_settings
from src.utils.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)

_DATA_DIRS = {
    "Startups": "startups",
    "Products": "products",
    "Research Papers": "research",
    "Jobs": "jobs",
    "News": "news",
    "Entity Mapping Log": "mappings",
}


async def run(push_sheets: bool) -> int:
    settings = load_settings()
    configure_logging(ROOT / "logs", settings.log_level)

    engine = create_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    tabs = await get_all_tabs(session_factory)
    await engine.dispose()

    for tab_name, rows in tabs.items():
        if not rows:
            continue
        subdir = _DATA_DIRS[tab_name]
        filename = "entity_mapping_log" if tab_name == "Entity Mapping Log" else subdir
        out_path = ROOT / "data" / subdir / f"{filename}.csv"
        write_csv(rows, TAB_HEADERS[tab_name], out_path)
        print(f"wrote {out_path}  ({len(rows)} rows)")

    workbook_path = ROOT / "data" / "pipeline_output_latest.xlsx"
    write_workbook(tabs, workbook_path)
    print(f"wrote {workbook_path}")

    if push_sheets:
        try:
            url, made_public = push_to_google_sheets(tabs, settings.google_service_account_file, settings.google_sheet_id)
            print(f"\nGoogle Sheet updated: {url}")
            if not made_public:
                print(
                    "WARNING: could not set 'anyone with the link can view' on this sheet "
                    "(the file owner has editors restricted from changing sharing settings). "
                    "Data was still written to all tabs -- share it manually: Share > General access > Anyone with the link."
                )
        except SheetsNotConfigured as e:
            print(f"\nGoogle Sheets not configured — skipped: {e}")
            print("See docs/GOOGLE_SHEETS_SETUP.md for the one-time credential setup.")
            return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Export pipeline database contents to CSV/XLSX/Google Sheets")
    parser.add_argument("--sheets", action="store_true", help="Also push to Google Sheets (requires service-account credentials)")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.sheets)))


if __name__ == "__main__":
    main()

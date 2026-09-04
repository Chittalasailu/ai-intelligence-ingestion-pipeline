"""Builds the 6 deliverable tabs (Startups, Products, Research Papers, Jobs,
News, Entity Mapping Log) as plain list-of-dict rows, then writes them to
both per-tab CSVs (data/<vertical>/*.csv) and one combined .xlsx workbook
with frozen header rows — the same rows are what google_sheets.py uploads,
so the CSV/XLSX/Sheets outputs are always in sync by construction.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

TAB_HEADERS: dict[str, list[str]] = {
    "Startups": ["entityName", "employeeCount", "source.name", "source.url", "collectedAt"],
    "Products": ["startupName", "pricingModel", "source.name", "source.url", "collectedAt"],
    "Research Papers": ["title", "authors", "paper_url", "github_url", "github_stars", "published_date", "collectedAt"],
    "Jobs": ["company", "title", "role_family", "is_remote", "date", "source.name", "source.url", "collectedAt"],
    "News": ["headline", "summary", "date", "source.name", "source.url", "collectedAt"],
    "Entity Mapping Log": ["raw_name", "canonical_name", "method", "confidence", "source_url", "timestamp"],
}


def write_csv(rows: list[dict[str, Any]], headers: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})


def _write_sheet(ws: Worksheet, rows: list[dict[str, Any]], headers: list[str]) -> None:
    ws.append(headers)
    ws.freeze_panes = "A2"
    for row in rows:
        ws.append([_stringify(row.get(h, "")) for h in headers])
    for i, header in enumerate(headers, start=1):
        col_letter = get_column_letter(i)
        max_len = max([len(header)] + [len(str(r.get(header, ""))) for r in rows[:200]])
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 60)


def _stringify(value: Any) -> Any:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value


def write_workbook(tabs: dict[str, list[dict[str, Any]]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for tab_name, headers in TAB_HEADERS.items():
        ws = wb.create_sheet(title=tab_name[:31])
        _write_sheet(ws, tabs.get(tab_name, []), headers)
    wb.save(path)

"""Flatten validated pydantic records into the flat dict shape the CSV/XLSX/
Google Sheets exporters (src/export/tabular.py TAB_HEADERS) expect.
"""
from __future__ import annotations

from typing import Any

from src.schemas.models import JobRecord, NewsRecord, ProductRecord, ResearchPaperRecord, StartupRecord


def startup_to_row(r: StartupRecord) -> dict[str, Any]:
    return {
        "entityName": r.content.entityName,
        "employeeCount": r.content.data.employeeCount if r.content.data.employeeCount is not None else "",
        "source.name": r.source.name,
        "source.url": str(r.source.url),
        "collectedAt": r.collectedAt.isoformat(),
    }


def product_to_row(r: ProductRecord) -> dict[str, Any]:
    return {
        "startupName": r.content.startupName,
        "pricingModel": r.content.pricingModel.value,
        "source.name": r.source.name,
        "source.url": str(r.source.url),
        "collectedAt": r.collectedAt.isoformat(),
    }


def paper_to_row(r: ResearchPaperRecord) -> dict[str, Any]:
    return {
        "title": r.content.title,
        "authors": ", ".join(r.content.authors),
        "paper_url": str(r.content.paper_url),
        "github_url": str(r.content.github_url) if r.content.github_url else "",
        "github_stars": r.content.github_stars if r.content.github_stars is not None else "",
        "published_date": r.content.published_date.isoformat(),
        "collectedAt": r.collectedAt.isoformat(),
    }


def job_to_row(r: JobRecord) -> dict[str, Any]:
    return {
        "company": r.content.company,
        "title": r.content.title or "",
        "role_family": r.content.role_family,
        "is_remote": r.content.is_remote,
        "date": r.content.date.isoformat(),
        "source.name": r.source.name,
        "source.url": str(r.source.url),
        "collectedAt": r.collectedAt.isoformat(),
    }


def news_to_row(r: NewsRecord) -> dict[str, Any]:
    return {
        "headline": r.content.headline,
        "summary": r.content.summary or "",
        "date": r.content.date.isoformat(),
        "source.name": r.source.name,
        "source.url": str(r.source.url),
        "collectedAt": r.collectedAt.isoformat(),
    }

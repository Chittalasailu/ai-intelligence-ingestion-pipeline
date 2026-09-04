"""Job board adapters: RemoteOK (JSON API), WeWorkRemotely (RSS), and
Greenhouse (public per-company JSON board API). Three different transports,
one normalized `RawJob` output — adding a 6th board is one adapter function,
not a pipeline rewrite.
"""
from __future__ import annotations

import html as html_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import feedparser

from src.utils.chunking import clean_html
from src.utils.freshness import parse_datetime
from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class RawJob:
    title: str
    company: str
    url: str
    posted_date: Optional[datetime]
    is_remote: bool
    description_text: str
    source_name: str


def _matches_keywords(text: str, keywords: Optional[list[str]]) -> bool:
    if not keywords:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


class RemoteOkAdapter:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def fetch(self, url: str, source_name: str, keyword_filter: Optional[list[str]] = None) -> list[RawJob]:
        status, body, _headers = await self.http_client.fetch(url, expect_json=True, headers={"User-Agent": "Mozilla/5.0"}, max_retries_override=2)
        if status != 200 or not isinstance(body, list):
            logger.warning("remoteok_fetch_failed", status=status)
            return []
        jobs: list[RawJob] = []
        for item in body:
            if not isinstance(item, dict) or "position" not in item:
                continue  # skips the legal-notice sentinel record RemoteOK prepends
            title = item.get("position", "")
            company = item.get("company", "")
            tags = " ".join(item.get("tags", []) or [])
            haystack = f"{title} {tags} {item.get('description', '')}"
            if not _matches_keywords(haystack, keyword_filter):
                continue
            posted = parse_datetime(item.get("date")) or parse_datetime(str(item.get("epoch", "")))
            jobs.append(
                RawJob(
                    title=title,
                    company=company,
                    url=item.get("url") or item.get("apply_url", ""),
                    posted_date=posted,
                    is_remote=True,
                    description_text=clean_html(item.get("description", ""))[:2000],
                    source_name=source_name,
                )
            )
        return jobs


class WwrRssAdapter:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def fetch(self, url: str, source_name: str, keyword_filter: Optional[list[str]] = None) -> list[RawJob]:
        status, body, _headers = await self.http_client.fetch(url, headers={"User-Agent": "Mozilla/5.0"}, max_retries_override=2)
        if status != 200 or not isinstance(body, str):
            logger.warning("wwr_fetch_failed", status=status)
            return []
        parsed = feedparser.parse(body)
        jobs: list[RawJob] = []
        for entry in parsed.entries:
            raw_title = entry.get("title", "")
            company, _, role = raw_title.partition(":")
            title = role.strip() or raw_title
            company = company.strip() if role else "Unknown"
            summary_text = clean_html(entry.get("summary", ""))
            if not _matches_keywords(f"{raw_title} {summary_text}", keyword_filter):
                continue
            jobs.append(
                RawJob(
                    title=title,
                    company=company,
                    url=entry.get("link", ""),
                    posted_date=parse_datetime(entry.get("published")),
                    is_remote=True,
                    description_text=summary_text[:2000],
                    source_name=source_name,
                )
            )
        return jobs


class GreenhouseAdapter:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def fetch(self, url: str, source_name: str, company_name: str, keyword_filter: Optional[list[str]] = None) -> list[RawJob]:
        status, body, _headers = await self.http_client.fetch(url, expect_json=True, max_retries_override=2)
        if status != 200 or not isinstance(body, dict):
            logger.warning("greenhouse_fetch_failed", source=source_name, status=status)
            return []
        jobs: list[RawJob] = []
        for item in body.get("jobs", []):
            title = item.get("title", "")
            content_raw = item.get("content", "") or ""
            content_text = clean_html(html_module.unescape(content_raw))
            location_name = (item.get("location") or {}).get("name", "")
            if not _matches_keywords(f"{title} {content_text}", keyword_filter):
                continue
            is_remote = "remote" in location_name.lower()
            posted = parse_datetime(item.get("first_published")) or parse_datetime(item.get("updated_at"))
            jobs.append(
                RawJob(
                    title=title,
                    company=item.get("company_name") or company_name,
                    url=item.get("absolute_url", ""),
                    posted_date=posted,
                    is_remote=is_remote,
                    description_text=content_text[:2000],
                    source_name=source_name,
                )
            )
        return jobs

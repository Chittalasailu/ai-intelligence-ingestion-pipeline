"""RSS/Atom news extractor with full-text enrichment.

Every configured feed (config/sources.yaml -> news:) is a publisher's own
official syndication feed — this is exactly what RSS is for, not a
bot-protection workaround. `pubDate`/`published` on each entry gives us a
real, source-declared timestamp, which is what makes the 24-hour freshness
guarantee possible to enforce honestly instead of via heuristics.
"""
from __future__ import annotations

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
class NewsItem:
    headline: str
    url: str
    published_date: Optional[datetime]
    summary: str
    source_name: str


class NewsRssExtractor:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def fetch_feed(self, feed_url: str, source_name: str, keyword_filter: Optional[list[str]] = None) -> list[NewsItem]:
        status, body, _headers = await self.http_client.fetch(feed_url, max_retries_override=2)
        if status != 200 or not isinstance(body, str):
            logger.warning("news_feed_fetch_failed", source=source_name, status=status)
            return []

        parsed = feedparser.parse(body)
        items: list[NewsItem] = []
        for entry in parsed.entries:
            title = " ".join(entry.get("title", "").split())
            link = entry.get("link", "")
            if not title or not link:
                continue
            if keyword_filter:
                haystack = (title + " " + entry.get("summary", "")).lower()
                if not any(kw.lower() in haystack for kw in keyword_filter):
                    continue
            published_raw = entry.get("published") or entry.get("updated") or entry.get("pubDate")
            published_date = parse_datetime(published_raw)
            summary_html = entry.get("summary", "")
            summary_text = clean_html(summary_html) if summary_html else ""
            items.append(
                NewsItem(
                    headline=title,
                    url=link,
                    published_date=published_date,
                    summary=summary_text[:1000],
                    source_name=source_name,
                )
            )
        return items

    async def fetch_full_text(self, article_url: str) -> Optional[str]:
        status, html, _headers = await self.http_client.fetch(article_url, max_retries_override=1)
        if status != 200 or not isinstance(html, str):
            return None
        return clean_html(html)

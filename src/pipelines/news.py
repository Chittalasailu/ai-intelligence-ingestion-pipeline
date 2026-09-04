"""News pipeline: 5 AI-news RSS feeds -> full-text-enriched NewsRecord rows,
strictly gated on the 24-hour freshness window.

A missing or unparseable publish date is a rejection, never a pass — see
src/utils/freshness.is_fresh. This is what keeps the "guaranteed last 24
hours" promise honest instead of aspirational.
"""
from __future__ import annotations

from typing import Any

from src.crawler.base import run_bounded
from src.extractors.news_rss import NewsItem, NewsRssExtractor
from src.pipelines.common import news_to_row
from src.pipelines.context import RunContext
from src.schemas.models import NewsRecord, utc_now
from src.schemas.validation import validate_record
from src.storage.repository import insert_news
from src.utils.dedup import content_hash
from src.utils.freshness import is_fresh
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

NAMESPACE = "news"


async def run_news_pipeline(ctx: RunContext, fetch_full_text: bool = True) -> list[dict[str, Any]]:
    source_cfgs = ctx.settings.source_group("news")
    extractor = NewsRssExtractor(ctx.http_client)
    window_hours = ctx.settings.freshness_window_hours

    all_items: list[NewsItem] = []
    for src in source_cfgs:
        items = await extractor.fetch_feed(src["url"], src["name"], src.get("keyword_filter"))
        ctx.stats.incr("records_discovered", len(items))
        all_items.extend(items)
        logger.info("news_feed_fetched", source=src["name"], items=len(items))

    async def _process(item: NewsItem) -> dict[str, Any] | None:
        if not is_fresh(item.published_date, window_hours):
            ctx.stats.incr("freshness_failures")
            ctx.rejection_log.reject(
                "NEWS",
                f"stale_or_unparseable_date (parsed={item.published_date.isoformat() if item.published_date else None})",
                {"headline": item.headline, "url": item.url},
                item.url,
            )
            return None

        if await ctx.checkpoint.has_seen(NAMESPACE, item.url):
            ctx.stats.incr("duplicates_removed")
            return None
        chash = content_hash(item.headline, item.source_name)
        if await ctx.checkpoint.has_content_hash(chash):
            ctx.stats.incr("duplicates_removed")
            return None

        ctx.stats.incr("records_fetched")
        full_text = None
        if fetch_full_text:
            full_text = await extractor.fetch_full_text(item.url)
        ctx.stats.incr("records_parsed")

        payload = {
            "schemaVersion": "1.0",
            "recordType": "NEWS",
            "source": {"name": item.source_name, "url": item.url},
            "content": {
                "headline": item.headline,
                "date": item.published_date.isoformat(),
                "summary": item.summary,
                "full_text": full_text,
                "url": item.url,
            },
            "collectedAt": utc_now().isoformat(),
        }
        record = validate_record(NewsRecord, payload, ctx.stats, ctx.rejection_log, "NEWS", item.url)
        if record is None:
            return None

        async with ctx.session_factory() as session:
            inserted = await insert_news(session, record)
            if not inserted:
                ctx.stats.incr("duplicates_removed")
                return None
        await ctx.checkpoint.mark_seen(NAMESPACE, item.url, utc_now().isoformat())
        await ctx.checkpoint.mark_content_hash(chash, NAMESPACE, utc_now().isoformat())
        return news_to_row(record)

    results = await run_bounded(all_items, _process, ctx.settings.max_concurrency)
    rows = [r for r in results if r]
    logger.info("news_pipeline_done", fresh_collected=len(rows), total_seen=len(all_items))
    return rows

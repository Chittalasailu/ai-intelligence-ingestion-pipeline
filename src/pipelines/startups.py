"""Startups pipeline: YC OSS directory -> canonicalized StartupRecord rows.

One-time bulk fetch (the dataset is a single static JSON file), then
per-company canonicalization + validation + storage fanned out through the
same bounded-concurrency runner every other pipeline uses.
"""
from __future__ import annotations

from typing import Any, Optional

from src.crawler.base import run_bounded
from src.extractors.yc_startups import YcCompany, YcStartupsExtractor
from src.pipelines.common import startup_to_row
from src.pipelines.context import RunContext
from src.schemas.models import StartupRecord, utc_now
from src.schemas.validation import validate_record
from src.storage.repository import insert_startup
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

NAMESPACE = "startups"


async def fetch_ai_companies(ctx: RunContext, target: int, active_only: bool = False) -> list[YcCompany]:
    """Shared by the startups and products pipelines so the ~10MB YC dataset
    is downloaded once per run instead of twice.
    """
    source_cfgs = ctx.settings.source_group("startups")
    yc_cfg = next((s for s in source_cfgs if s["adapter"] == "yc_startups"), {})
    url = yc_cfg.get("url", "https://yc-oss.github.io/api/companies/all.json")

    extractor = YcStartupsExtractor(ctx.http_client, url)
    all_companies = await extractor.fetch_all()
    ctx.stats.incr("records_discovered", len(all_companies))

    # Pull a modest buffer beyond `target` so validation/dedup rejections
    # don't leave the final count short.
    candidate_limit = int(target * 1.2) + 50
    return extractor.filter_ai_companies(all_companies, limit=candidate_limit, active_only=active_only)


async def run_startups_pipeline(ctx: RunContext, target: int = 1000, companies: Optional[list[YcCompany]] = None) -> list[dict[str, Any]]:
    source_cfgs = ctx.settings.source_group("startups")
    yc_cfg = next((s for s in source_cfgs if s["adapter"] == "yc_startups"), {})
    source_name = yc_cfg.get("name", "YC OSS Startup Directory")

    ai_companies = companies if companies is not None else await fetch_ai_companies(ctx, target)

    async def _process(company: YcCompany) -> dict[str, Any] | None:
        dedup_id = company.name.strip().lower()
        if await ctx.checkpoint.has_seen(NAMESPACE, dedup_id):
            ctx.stats.incr("duplicates_removed")
            return None
        ctx.stats.incr("records_fetched")
        ctx.stats.incr("records_parsed")

        resolution = ctx.resolver.resolve(company.name)
        await ctx.record_entity_mapping(company.name, resolution, company.yc_page_url)

        payload = {
            "schemaVersion": "1.0",
            "recordType": "STARTUP",
            "source": {"name": source_name, "url": company.yc_page_url},
            "content": {
                "entityName": resolution.canonical_name,
                "data": {"employeeCount": company.team_size},
            },
            "collectedAt": utc_now().isoformat(),
        }
        record = validate_record(StartupRecord, payload, ctx.stats, ctx.rejection_log, "STARTUP", company.yc_page_url)
        if record is None:
            return None

        async with ctx.session_factory() as session:
            inserted = await insert_startup(session, record, resolution.canonical_name)
            if not inserted:
                ctx.stats.incr("duplicates_removed")
                return None
        # Only mark the checkpoint once the row is actually durable — marking
        # first would permanently skip this company on resume if the insert
        # below ever failed (e.g. a transient "database is locked").
        await ctx.checkpoint.mark_seen(NAMESPACE, dedup_id, utc_now().isoformat())
        return startup_to_row(record)

    results = await run_bounded(ai_companies, _process, ctx.settings.max_concurrency)
    rows = [r for r in results if r]
    logger.info("startups_pipeline_done", collected=len(rows), target=target, candidates=len(ai_companies))
    return rows[:target] if len(rows) > target else rows

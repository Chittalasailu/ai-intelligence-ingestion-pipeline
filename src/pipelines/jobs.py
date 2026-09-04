"""Jobs pipeline: 5 job boards (RemoteOK, WeWorkRemotely, 3x Greenhouse) ->
role_family-classified JobRecord rows, gated on the same 24-hour freshness
window as news.

Realistic expectation, stated up front rather than glossed over: RemoteOK
and WeWorkRemotely post continuously, so they reliably yield same-day
results; Greenhouse boards only pass the freshness filter on days a company
actually published or touched a requisition, which is often zero on any
given day for any one board — see docs/LIMITATIONS.md. That's the freshness
gate doing its job, not a bug.
"""
from __future__ import annotations

from typing import Any

from src.crawler.base import run_bounded
from src.extractors.jobs import GreenhouseAdapter, RawJob, RemoteOkAdapter, WwrRssAdapter
from src.pipelines.common import job_to_row
from src.pipelines.context import RunContext
from src.schemas.models import JobRecord, utc_now
from src.schemas.validation import validate_record
from src.storage.repository import insert_job
from src.utils.freshness import is_fresh
from src.utils.logging_setup import get_logger
from src.utils.role_family import classify_role_family

logger = get_logger(__name__)

NAMESPACE = "jobs"

_ROLE_FAMILY_PROMPT = (
    "Classify this job posting's functional category from its title and description. "
    'Respond with ONLY JSON: {"role_family": "Engineering" | "Data Science / ML" | "Research" | '
    '"Product" | "Design" | "Sales" | "Marketing" | "Customer Support" | "Operations" | "Other"}.'
)


async def _classify_role_family(ctx: RunContext, title: str, description: str) -> str:
    if ctx.llm.configured_providers:
        result = await ctx.llm.extract_structured(_ROLE_FAMILY_PROMPT, f"Title: {title}\n\n{description[:2000]}", target_keys=["role_family"])
        if result.success and result.data and result.data.get("role_family"):
            return str(result.data["role_family"])
    return classify_role_family(title, description)


async def run_jobs_pipeline(ctx: RunContext) -> list[dict[str, Any]]:
    source_cfgs = ctx.settings.source_group("jobs")
    window_hours = ctx.settings.freshness_window_hours

    remoteok = RemoteOkAdapter(ctx.http_client)
    wwr = WwrRssAdapter(ctx.http_client)
    greenhouse = GreenhouseAdapter(ctx.http_client)

    all_jobs: list[tuple[RawJob, str, str]] = []  # (job, source_name, source_url)
    for src in source_cfgs:
        adapter_name = src["adapter"]
        try:
            if adapter_name == "remoteok":
                jobs = await remoteok.fetch(src["url"], src["name"], src.get("keyword_filter"))
            elif adapter_name == "rss_jobs":
                jobs = await wwr.fetch(src["url"], src["name"], src.get("keyword_filter"))
            elif adapter_name == "greenhouse":
                jobs = await greenhouse.fetch(src["url"], src["name"], src.get("board_token", ""), src.get("keyword_filter"))
            else:
                logger.warning("unknown_job_adapter", adapter=adapter_name)
                continue
        except Exception as e:  # noqa: BLE001 - one dead board must not kill the run
            logger.error("job_source_failed", source=src["name"], error=str(e))
            continue
        ctx.stats.incr("records_discovered", len(jobs))
        logger.info("job_source_fetched", source=src["name"], jobs=len(jobs))
        all_jobs.extend((j, src["name"], src["url"]) for j in jobs)

    async def _process(item: tuple[RawJob, str, str]) -> dict[str, Any] | None:
        job, source_name, source_url = item
        if not is_fresh(job.posted_date, window_hours):
            ctx.stats.incr("freshness_failures")
            ctx.rejection_log.reject(
                "JOB",
                f"stale_or_unparseable_date (parsed={job.posted_date.isoformat() if job.posted_date else None})",
                {"title": job.title, "company": job.company, "url": job.url},
                job.url,
            )
            return None

        dedup_id = job.url or f"{job.company}:{job.title}"
        if await ctx.checkpoint.has_seen(NAMESPACE, dedup_id):
            ctx.stats.incr("duplicates_removed")
            return None

        ctx.stats.incr("records_fetched")
        ctx.stats.incr("records_parsed")

        role_family = await _classify_role_family(ctx, job.title, job.description_text)
        resolution = ctx.resolver.resolve(job.company)
        await ctx.record_entity_mapping(job.company, resolution, job.url)

        payload = {
            "schemaVersion": "1.0",
            "recordType": "JOB",
            "source": {"name": source_name, "url": job.url or source_url},
            "content": {
                "company": resolution.canonical_name,
                "date": job.posted_date.isoformat(),
                "is_remote": job.is_remote,
                "role_family": role_family,
                "title": job.title,
                "url": job.url or None,
            },
            "collectedAt": utc_now().isoformat(),
        }
        record = validate_record(JobRecord, payload, ctx.stats, ctx.rejection_log, "JOB", job.url)
        if record is None:
            return None

        async with ctx.session_factory() as session:
            inserted = await insert_job(session, record, resolution.canonical_name)
            if not inserted:
                ctx.stats.incr("duplicates_removed")
                return None
        await ctx.checkpoint.mark_seen(NAMESPACE, dedup_id, utc_now().isoformat())
        return job_to_row(record)

    results = await run_bounded(all_jobs, _process, ctx.settings.max_concurrency)
    rows = [r for r in results if r]
    logger.info("jobs_pipeline_done", fresh_collected=len(rows), total_seen=len(all_jobs))
    return rows

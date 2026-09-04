"""Research papers pipeline: arXiv (primary, high-volume) + Hugging Face
Daily Papers (secondary, curated/trending) -> validated ResearchPaperRecord
rows, with GitHub star enrichment only where the paper itself declares a
repo URL.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any

from src.crawler.base import run_bounded
from src.extractors.arxiv import ArxivExtractor, ArxivPaper
from src.extractors.github_stars import GithubStarsCache, GithubStarsClient
from src.extractors.hf_papers import HfDailyPapersExtractor
from src.pipelines.common import paper_to_row
from src.pipelines.context import RunContext
from src.schemas.models import ResearchPaperRecord, utc_now
from src.schemas.validation import is_valid_github_evidence, validate_record
from src.utils.config import ROOT
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

NAMESPACE = "research_papers"


async def _handle_candidate(
    ctx: RunContext,
    github_client: GithubStarsClient,
    arxiv_id: str,
    title: str,
    authors: list[str],
    paper_url: str,
    published_date: datetime,
    github_url_candidate: str | None,
    github_evidence_text: str | None,
    source_name: str,
) -> dict[str, Any] | None:
    ctx.stats.incr("records_discovered")

    if await ctx.checkpoint.has_seen(NAMESPACE, arxiv_id):
        ctx.stats.incr("duplicates_removed")
        return None
    ctx.stats.incr("records_fetched")
    ctx.stats.incr("records_parsed")

    github_url: str | None = None
    github_stars: int | None = None
    if github_url_candidate and is_valid_github_evidence(github_url_candidate, github_evidence_text):
        github_url = github_url_candidate
        github_stars = await github_client.get_stars(github_url)

    payload = {
        "schemaVersion": "1.0",
        "recordType": "RESEARCH_PAPER",
        "source": {"name": source_name, "url": paper_url},
        "content": {
            "title": title,
            "authors": authors,
            "paper_url": paper_url,
            "github_url": github_url,
            "github_stars": github_stars,
            "published_date": published_date.isoformat(),
        },
        "collectedAt": utc_now().isoformat(),
    }
    record = validate_record(ResearchPaperRecord, payload, ctx.stats, ctx.rejection_log, "RESEARCH_PAPER", paper_url)
    if record is None:
        return None

    async with ctx.session_factory() as session:
        from src.storage.repository import insert_research_paper

        inserted = await insert_research_paper(session, record)
        if not inserted:
            ctx.stats.incr("duplicates_removed")
            return None
    await ctx.checkpoint.mark_seen(NAMESPACE, arxiv_id, utc_now().isoformat())

    return paper_to_row(record)


async def run_research_papers_pipeline(ctx: RunContext, target: int = 1000) -> list[dict[str, Any]]:
    source_cfgs = ctx.settings.source_group("research_papers")
    arxiv_cfg = next((s for s in source_cfgs if s["adapter"] == "arxiv"), {})
    hf_cfg = next((s for s in source_cfgs if s["adapter"] == "hf_daily_papers"), {})

    categories: list[str] = arxiv_cfg.get("categories", ["cs.AI", "cs.LG"])
    page_size = int(arxiv_cfg.get("page_size", 100))
    polite_delay = float(arxiv_cfg.get("polite_delay_seconds", 3.0))

    arxiv = ArxivExtractor(ctx.http_client, arxiv_cfg.get("base_url", "https://export.arxiv.org/api/query"), polite_delay)
    hf = HfDailyPapersExtractor(ctx.http_client, hf_cfg.get("url", "https://huggingface.co/api/daily_papers"))
    github_cache = GithubStarsCache(ROOT / "data" / "mappings" / "github_stars_cache.json")
    github_client = GithubStarsClient(ctx.http_client, ctx.settings.github_token, github_cache)

    rows: list[dict[str, Any]] = []
    per_category_target = max(1, math.ceil((target * 1.15) / max(len(categories), 1)))

    async def _process_arxiv_paper(paper: ArxivPaper) -> dict[str, Any] | None:
        return await _handle_candidate(
            ctx,
            github_client,
            paper.arxiv_id,
            paper.title,
            paper.authors,
            paper.paper_url,
            paper.published_date,
            paper.github_url_candidate,
            paper.github_evidence_text,
            "arXiv API",
        )

    for category in categories:
        if len(rows) >= target:
            break
        logger.info("arxiv_category_start", category=category, per_category_target=per_category_target)
        fetched_for_category = 0
        start = 0
        while fetched_for_category < per_category_target and len(rows) < target:
            page = await arxiv.fetch_category(category, start, min(page_size, per_category_target - fetched_for_category))
            if not page:
                break
            # arXiv's own /api/query must be hit sequentially (politeness delay
            # below); everything AFTER the fetch — GitHub lookups, validation,
            # DB writes — is independent per paper, so that part runs through
            # the same bounded-concurrency machinery the rest of the crawler
            # uses, rather than one-at-a-time.
            page_results = await run_bounded(page, _process_arxiv_paper, ctx.settings.max_concurrency)
            rows.extend(r for r in page_results if r)
            fetched_for_category += len(page)
            start += len(page)
            await asyncio.sleep(arxiv.polite_delay_seconds)

    if len(rows) < target:
        logger.info("supplementing_with_hf_daily_papers", have=len(rows), target=target)
        hf_papers = await hf.fetch(limit=100)
        for paper in hf_papers:
            if len(rows) >= target:
                break
            row = await _handle_candidate(
                ctx,
                github_client,
                paper.arxiv_id,
                paper.title,
                paper.authors,
                paper.paper_url,
                paper.published_date,
                paper.github_url_candidate,
                paper.github_evidence_text,
                "Hugging Face Daily Papers",
            )
            if row:
                rows.append(row)

    logger.info("research_papers_pipeline_done", collected=len(rows), target=target)
    return rows

"""CLI entrypoint. Examples:

  python -m src.main --pipeline all --startups-target 1000 --products-target 1000 --papers-target 1000
  python -m src.main --pipeline research_papers --papers-target 50
  python -m src.main --pipeline news
  python -m src.main --pipeline jobs

See README.md "Running" section for the full command list.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.crawler.base import GracefulShutdown
from src.export.tabular import TAB_HEADERS, write_csv, write_workbook
from src.llm.factory import build_orchestrator
from src.pipelines.context import build_run_context
from src.pipelines.jobs import run_jobs_pipeline
from src.pipelines.news import run_news_pipeline
from src.pipelines.products import run_products_pipeline
from src.pipelines.research_papers import run_research_papers_pipeline
from src.pipelines.startups import fetch_ai_companies, run_startups_pipeline
from src.storage.db import create_engine, init_db, make_session_factory
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FrontierAtlas AI Intelligence Ingestion Pipeline")
    p.add_argument(
        "--pipeline",
        choices=["all", "startups", "products", "research_papers", "news", "jobs"],
        default="all",
    )
    p.add_argument("--startups-target", type=int, default=1000)
    p.add_argument("--products-target", type=int, default=1000)
    p.add_argument("--papers-target", type=int, default=1000)
    p.add_argument("--no-full-text", action="store_true", help="Skip fetching full article text for news (faster, less polite load on sources).")
    p.add_argument("--run-id", default=None)
    return p.parse_args(argv)


async def main_async(argv: list[str]) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    configure_logging(ROOT / "logs", settings.log_level)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("pipeline_run_start", run_id=run_id, pipeline=args.pipeline)

    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    shutdown = GracefulShutdown()
    shutdown.install_signal_handlers()

    tabs: dict[str, list[dict]] = {name: [] for name in TAB_HEADERS}

    async with build_orchestrator(settings) as llm:
        ctx = build_run_context(settings, session_factory, llm, run_id)
        async with ctx.http_client:
            need_yc = args.pipeline in ("all", "startups", "products")
            companies = None
            if need_yc:
                companies = await fetch_ai_companies(ctx, max(args.startups_target, args.products_target))

            if args.pipeline in ("all", "startups"):
                tabs["Startups"] = await run_startups_pipeline(ctx, args.startups_target, companies)
            if shutdown.is_set:
                logger.warning("shutdown_requested_stopping_early")
            elif args.pipeline in ("all", "products"):
                tabs["Products"] = await run_products_pipeline(ctx, args.products_target, companies)

            if not shutdown.is_set and args.pipeline in ("all", "research_papers"):
                tabs["Research Papers"] = await run_research_papers_pipeline(ctx, args.papers_target)

            if not shutdown.is_set and args.pipeline in ("all", "news"):
                tabs["News"] = await run_news_pipeline(ctx, fetch_full_text=not args.no_full_text)

            if not shutdown.is_set and args.pipeline in ("all", "jobs"):
                tabs["Jobs"] = await run_jobs_pipeline(ctx)

            tabs["Entity Mapping Log"] = ctx.mapping_log.rows

        stats_dict = ctx.stats.as_dict()
        llm_usage = llm.usage_report()

    # Export reflects the full accumulated database, not just this run's new
    # rows — a resumed run correctly finds prior records "already seen" and
    # contributes zero *new* rows, which must not shrink the exported file.
    from src.storage.repository import get_all_tabs

    export_tabs = await get_all_tabs(session_factory)
    await engine.dispose()

    _write_outputs(export_tabs, run_id)
    _print_summary(args.pipeline, tabs, export_tabs, stats_dict, llm_usage)
    return 0


def _write_outputs(tabs: dict[str, list[dict]], run_id: str) -> None:
    for tab_name, rows in tabs.items():
        if not rows:
            continue
        subdir = _DATA_DIRS[tab_name]
        filename = "entity_mapping_log" if tab_name == "Entity Mapping Log" else subdir
        out_path = ROOT / "data" / subdir / f"{filename}.csv"
        write_csv(rows, TAB_HEADERS[tab_name], out_path)
        logger.info("wrote_csv", tab=tab_name, path=str(out_path), rows=len(rows))

    # Stable filename (not per-run_id) so repeated runs overwrite one
    # workbook instead of accumulating a new multi-hundred-KB file every
    # invocation — the CSVs above are the versioned per-tab data either way.
    workbook_path = ROOT / "data" / "pipeline_output_latest.xlsx"
    write_workbook(tabs, workbook_path)
    logger.info("wrote_workbook", path=str(workbook_path))


def _print_summary(pipeline: str, tabs: dict[str, list[dict]], export_tabs: dict[str, list[dict]], stats: dict, llm_usage: dict) -> None:
    print("\n" + "=" * 60)
    print(f"PIPELINE RUN COMPLETE ({pipeline})")
    print("=" * 60)
    print("\nRecords per tab (new this run / total in database):")
    for tab_name in export_tabs:
        new_count = len(tabs.get(tab_name, []))
        total_count = len(export_tabs[tab_name])
        print(f"  {tab_name:<20} {new_count:>6} new   {total_count:>6} total")
    print("\nQuality stats (this run):")
    for k, v in stats.items():
        print(f"  {k:<22} {v}")
    print("\nLLM usage (estimated tokens):")
    if not llm_usage:
        print("  (no LLM providers configured / invoked)")
    for provider, usage in llm_usage.items():
        print(f"  {provider:<10} prompt~{usage['prompt_tokens_est']}  completion~{usage['completion_tokens_est']}")
    print()


def main() -> None:
    try:
        exit_code = asyncio.run(main_async(sys.argv[1:]))
    except KeyboardInterrupt:
        logger.warning("interrupted_by_user")
        exit_code = 130
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

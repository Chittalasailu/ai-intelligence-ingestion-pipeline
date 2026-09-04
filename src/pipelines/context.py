"""Shared run context: everything a pipeline needs, built once per run and
passed down instead of each pipeline re-wiring its own dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.entity_resolution.mapping_log import MappingLogWriter
from src.entity_resolution.resolver import EntityResolver, MappingLogEntry, ResolutionResult
from src.llm.orchestrator import LLMOrchestrator
from src.schemas.validation import QualityStats, RejectionLog
from src.utils.checkpoint import CheckpointStore
from src.utils.config import ROOT, Settings
from src.utils.http_client import AsyncHttpClient
from src.utils.retry import BackoffConfig


@dataclass
class RunContext:
    settings: Settings
    http_client: AsyncHttpClient
    stats: QualityStats
    rejection_log: RejectionLog
    checkpoint: CheckpointStore
    resolver: EntityResolver
    mapping_log: MappingLogWriter
    session_factory: async_sessionmaker
    llm: LLMOrchestrator

    async def record_entity_mapping(self, raw_name: str, resolution: ResolutionResult, source_url: str | None) -> None:
        """Single call site for both persistence paths: the CSV (human-
        readable, incrementally correct across process restarts — see
        MappingLogWriter) and the DB (queried by scripts/export_data.py so
        exports work without re-running any pipeline).
        """
        entry = MappingLogEntry.build(raw_name, resolution, source_url)
        self.mapping_log.write(entry)

        from src.storage.repository import insert_entity_mapping

        async with self.session_factory() as session:
            await insert_entity_mapping(session, entry)


def build_run_context(
    settings: Settings,
    session_factory: async_sessionmaker,
    llm: LLMOrchestrator,
    run_id: str,
    data_root: Path | None = None,
) -> RunContext:
    """`data_root` defaults to the real project ROOT; tests pass a tmp_path
    here so checkpoint/rejection/mapping-log state never leaks into (or gets
    polluted by) the real repo's data/logs directories between test runs.
    """
    root = data_root or ROOT
    backoff = BackoffConfig(max_retries=settings.max_retries)
    http_client = AsyncHttpClient(
        max_concurrency=settings.max_concurrency,
        per_host_concurrency=settings.per_host_concurrency,
        timeout_seconds=settings.request_timeout_seconds,
        user_agent=settings.raw_settings.get("crawler", {}).get("user_agent", "FrontierAtlasIntelligenceBot/1.0"),
        backoff=backoff,
    )
    stats = QualityStats()
    rejection_log = RejectionLog(root / "logs" / f"rejected_{run_id}.jsonl")
    checkpoint = CheckpointStore(root / "data" / "checkpoints.sqlite")
    resolver = EntityResolver(
        fuzzy_threshold=settings.raw_settings.get("entity_resolution", {}).get("fuzzy_match_threshold", 90),
        review_threshold=settings.raw_settings.get("entity_resolution", {}).get("review_threshold", 80),
    )
    mapping_log = MappingLogWriter(root / "data" / "mappings" / "entity_mapping_log.csv")

    return RunContext(
        settings=settings,
        http_client=http_client,
        stats=stats,
        rejection_log=rejection_log,
        checkpoint=checkpoint,
        resolver=resolver,
        mapping_log=mapping_log,
        session_factory=session_factory,
        llm=llm,
    )

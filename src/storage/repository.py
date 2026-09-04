"""Insert-with-dedup repository functions, one per record type.

Every insert function computes a content hash, tries the insert, and treats
a unique-constraint violation on `dedup_key` as "already have this" rather
than an error — the database is a second, authoritative dedup layer behind
the crawler's own checkpoint-based dedup (which protects against re-fetching,
not against two different sources yielding the same entity).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.schemas.models import JobRecord, NewsRecord, ProductRecord, ResearchPaperRecord, StartupRecord
from src.storage.models import EntityMappingORM, JobORM, NewsORM, ProductORM, ResearchPaperORM, StartupORM
from src.utils.dedup import content_hash
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


async def _commit_or_duplicate(session: AsyncSession, orm_obj) -> bool:
    session.add(orm_obj)
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def insert_startup(session: AsyncSession, record: StartupRecord, canonical_name: str) -> bool:
    key = content_hash(canonical_name, str(record.source.url))
    orm_obj = StartupORM(
        dedup_key=key,
        schema_version=record.schemaVersion,
        record_type=record.recordType,
        source_name=record.source.name,
        source_url=str(record.source.url),
        entity_name=record.content.entityName,
        canonical_name=canonical_name,
        employee_count=record.content.data.employeeCount,
        collected_at=record.collectedAt,
    )
    return await _commit_or_duplicate(session, orm_obj)


async def insert_product(session: AsyncSession, record: ProductRecord, canonical_name: str) -> bool:
    key = content_hash(canonical_name, str(record.source.url), record.content.pricingModel.value)
    orm_obj = ProductORM(
        dedup_key=key,
        schema_version=record.schemaVersion,
        record_type=record.recordType,
        source_name=record.source.name,
        source_url=str(record.source.url),
        startup_name=record.content.startupName,
        canonical_name=canonical_name,
        pricing_model=record.content.pricingModel.value,
        collected_at=record.collectedAt,
    )
    return await _commit_or_duplicate(session, orm_obj)


async def insert_research_paper(session: AsyncSession, record: ResearchPaperRecord) -> bool:
    key = content_hash(record.content.title, str(record.content.paper_url))
    orm_obj = ResearchPaperORM(
        dedup_key=key,
        schema_version=record.schemaVersion,
        record_type=record.recordType,
        source_name=record.source.name if record.source else "",
        source_url=str(record.source.url) if record.source else "",
        title=record.content.title,
        authors=record.content.authors,
        paper_url=str(record.content.paper_url),
        github_url=str(record.content.github_url) if record.content.github_url else None,
        github_stars=record.content.github_stars,
        published_date=record.content.published_date,
        collected_at=record.collectedAt,
    )
    return await _commit_or_duplicate(session, orm_obj)


async def insert_job(session: AsyncSession, record: JobRecord, canonical_company: str) -> bool:
    key = content_hash(canonical_company, record.content.role_family, str(record.content.url or ""), record.content.date.isoformat())
    orm_obj = JobORM(
        dedup_key=key,
        schema_version=record.schemaVersion,
        record_type=record.recordType,
        source_name=record.source.name,
        source_url=str(record.source.url),
        company=record.content.company,
        canonical_company=canonical_company,
        title=record.content.title or "",
        role_family=record.content.role_family,
        is_remote=record.content.is_remote,
        job_date=record.content.date,
        collected_at=record.collectedAt,
    )
    return await _commit_or_duplicate(session, orm_obj)


async def insert_news(session: AsyncSession, record: NewsRecord) -> bool:
    key = content_hash(record.content.headline, str(record.content.url))
    orm_obj = NewsORM(
        dedup_key=key,
        schema_version=record.schemaVersion,
        record_type=record.recordType,
        source_name=record.source.name,
        source_url=str(record.source.url),
        headline=record.content.headline,
        summary=record.content.summary or "",
        news_date=record.content.date,
        collected_at=record.collectedAt,
    )
    return await _commit_or_duplicate(session, orm_obj)


async def insert_entity_mapping(session: AsyncSession, entry) -> bool:
    """Same raw_name/canonical_name/method combo is a duplicate regardless
    of which pipeline (or which process invocation) resolved it — startups
    and products both walk the same YC company list, so this fires
    constantly and must be a no-op, not an error.
    """
    key = content_hash(entry.raw_name, entry.canonical_name, entry.method)
    orm_obj = EntityMappingORM(
        dedup_key=key,
        raw_name=entry.raw_name,
        canonical_name=entry.canonical_name,
        method=entry.method,
        confidence=entry.confidence,
        source_url=entry.source_url,
        timestamp=entry.timestamp,
    )
    return await _commit_or_duplicate(session, orm_obj)


# -- full-table reads for export (CSV/XLSX/Sheets) --
#
# Exports must reflect the DATABASE, not one run's in-memory result list.
# Checkpointing means a second run of an already-populated pipeline
# correctly finds everything "already seen" and returns zero *new* rows —
# that's resumability working as intended, not a data loss. So the export
# step re-reads the accumulated table state every time instead of trusting
# whatever a single run happened to collect.


async def get_all_startup_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(StartupORM).order_by(StartupORM.id))
        rows = result.scalars().all()
    return [
        {
            "entityName": r.canonical_name,
            "employeeCount": r.employee_count if r.employee_count is not None else "",
            "source.name": r.source_name,
            "source.url": r.source_url,
            "collectedAt": r.collected_at.isoformat(),
        }
        for r in rows
    ]


async def get_all_product_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(ProductORM).order_by(ProductORM.id))
        rows = result.scalars().all()
    return [
        {
            "startupName": r.canonical_name,
            "pricingModel": r.pricing_model,
            "source.name": r.source_name,
            "source.url": r.source_url,
            "collectedAt": r.collected_at.isoformat(),
        }
        for r in rows
    ]


async def get_all_research_paper_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(ResearchPaperORM).order_by(ResearchPaperORM.id))
        rows = result.scalars().all()
    return [
        {
            "title": r.title,
            "authors": ", ".join(r.authors or []),
            "paper_url": r.paper_url,
            "github_url": r.github_url or "",
            "github_stars": r.github_stars if r.github_stars is not None else "",
            "published_date": r.published_date.isoformat(),
            "collectedAt": r.collected_at.isoformat(),
        }
        for r in rows
    ]


async def get_all_job_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(JobORM).order_by(JobORM.id))
        rows = result.scalars().all()
    return [
        {
            "company": r.canonical_company,
            "title": r.title,
            "role_family": r.role_family,
            "is_remote": r.is_remote,
            "date": r.job_date.isoformat(),
            "source.name": r.source_name,
            "source.url": r.source_url,
            "collectedAt": r.collected_at.isoformat(),
        }
        for r in rows
    ]


async def get_all_news_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(NewsORM).order_by(NewsORM.id))
        rows = result.scalars().all()
    return [
        {
            "headline": r.headline,
            "summary": r.summary,
            "date": r.news_date.isoformat(),
            "source.name": r.source_name,
            "source.url": r.source_url,
            "collectedAt": r.collected_at.isoformat(),
        }
        for r in rows
    ]


async def get_all_entity_mapping_rows(session_factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(select(EntityMappingORM).order_by(EntityMappingORM.id))
        rows = result.scalars().all()
    return [
        {
            "raw_name": r.raw_name,
            "canonical_name": r.canonical_name,
            "method": r.method,
            "confidence": r.confidence,
            "source_url": r.source_url or "",
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]


async def get_all_tabs(session_factory: async_sessionmaker) -> dict[str, list[dict[str, Any]]]:
    return {
        "Startups": await get_all_startup_rows(session_factory),
        "Products": await get_all_product_rows(session_factory),
        "Research Papers": await get_all_research_paper_rows(session_factory),
        "Jobs": await get_all_job_rows(session_factory),
        "News": await get_all_news_rows(session_factory),
        "Entity Mapping Log": await get_all_entity_mapping_rows(session_factory),
    }

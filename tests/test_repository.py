"""DB-layer tests against a real (temp-file) SQLite database — no mocks,
since the whole point is to catch type/constraint mismatches SQLAlchemy
only surfaces when it actually talks to a driver.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.entity_resolution.resolver import EntityResolver, MappingLogEntry
from src.schemas.models import JobRecord, NewsRecord
from src.storage.db import create_engine, init_db, make_session_factory
from src.storage.models import JobORM, NewsORM
from src.storage.repository import get_all_entity_mapping_rows, insert_entity_mapping, insert_job, insert_news
from sqlalchemy import select


async def _make_session_factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    await init_db(engine)
    return engine, make_session_factory(engine)


async def test_insert_entity_mapping_accepts_real_datetime(tmp_path):
    # Regression test: MappingLogEntry.timestamp used to be an ISO string
    # (fine for CSV) but SQLite's DateTime column rejects strings outright
    # ("SQLite DateTime type only accepts Python datetime and date objects")
    # — every single insert failed until timestamp became a real datetime.
    # Caught by actually running the startups pipeline against a real DB.
    engine, session_factory = await _make_session_factory(tmp_path)
    entry = MappingLogEntry.build("OpenAI Inc", EntityResolver().resolve("OpenAI Inc"), "https://example.com")

    async with session_factory() as session:
        inserted = await insert_entity_mapping(session, entry)

    assert inserted is True
    rows = await get_all_entity_mapping_rows(session_factory)
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "OpenAI"
    await engine.dispose()


async def test_insert_entity_mapping_dedups_same_resolution(tmp_path):
    engine, session_factory = await _make_session_factory(tmp_path)
    entry = MappingLogEntry.build("OpenAI Inc", EntityResolver().resolve("OpenAI Inc"), "https://example.com/a")
    entry_again = MappingLogEntry.build("OpenAI Inc", EntityResolver().resolve("OpenAI Inc"), "https://example.com/b")

    async with session_factory() as session:
        first = await insert_entity_mapping(session, entry)
    async with session_factory() as session:
        second = await insert_entity_mapping(session, entry_again)

    assert first is True
    assert second is False  # same raw/canonical/method -> duplicate, even from a "different" source URL
    rows = await get_all_entity_mapping_rows(session_factory)
    assert len(rows) == 1
    await engine.dispose()


async def test_insert_job_persists_full_description_text(tmp_path):
    # Regression test: the job pipeline fetched and cleaned full posting
    # text (used to classify role_family) but JobORM had no column to put
    # it in, so the Phase II "full-text content" extraction was silently
    # discarded after use instead of being preserved. Caught by inspecting
    # the database directly, not by any prior test — every schema test used
    # payloads that never checked whether this field survived to storage.
    engine, session_factory = await _make_session_factory(tmp_path)
    record = JobRecord.model_validate({
        "schemaVersion": "1.0",
        "recordType": "JOB",
        "source": {"name": "RemoteOK", "url": "https://remoteok.com/api"},
        "content": {
            "company": "Acme AI",
            "date": datetime.now(timezone.utc).isoformat(),
            "is_remote": True,
            "role_family": "Engineering",
            "title": "ML Engineer",
            "description": "Full job posting text goes here, in full.",
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    })

    async with session_factory() as session:
        inserted = await insert_job(session, record, "Acme AI")
    assert inserted is True

    async with session_factory() as session:
        row = (await session.execute(select(JobORM))).scalar_one()
    assert row.description_text == "Full job posting text goes here, in full."
    await engine.dispose()


async def test_insert_news_persists_full_article_text(tmp_path):
    engine, session_factory = await _make_session_factory(tmp_path)
    record = NewsRecord.model_validate({
        "schemaVersion": "1.0",
        "recordType": "NEWS",
        "source": {"name": "TechCrunch AI", "url": "https://techcrunch.com/foo"},
        "content": {
            "headline": "Big AI news",
            "date": datetime.now(timezone.utc).isoformat(),
            "summary": "Short summary.",
            "full_text": "The complete cleaned article body goes here.",
            "url": "https://techcrunch.com/foo",
        },
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    })

    async with session_factory() as session:
        inserted = await insert_news(session, record)
    assert inserted is True

    async with session_factory() as session:
        row = (await session.execute(select(NewsORM))).scalar_one()
    assert row.full_text == "The complete cleaned article body goes here."
    await engine.dispose()

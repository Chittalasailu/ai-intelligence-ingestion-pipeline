"""DB-layer tests against a real (temp-file) SQLite database — no mocks,
since the whole point is to catch type/constraint mismatches SQLAlchemy
only surfaces when it actually talks to a driver.
"""
from __future__ import annotations

from src.entity_resolution.resolver import EntityResolver, MappingLogEntry
from src.storage.db import create_engine, init_db, make_session_factory
from src.storage.repository import get_all_entity_mapping_rows, insert_entity_mapping


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

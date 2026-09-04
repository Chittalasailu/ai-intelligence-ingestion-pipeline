"""Async engine/session setup. DATABASE_URL decides SQLite vs Postgres —
nothing else in the codebase needs to change between them.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.storage.models import Base


def create_engine(database_url: str) -> AsyncEngine:
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        db_path = database_url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(database_url, echo=False, future=True)

    if is_sqlite:
        # Concurrent async pipelines fan out many inserts at once (see
        # src/crawler/base.run_bounded). SQLite's default rollback-journal
        # mode locks the whole file per writer, so under real concurrency
        # writers collide and raise "database is locked" instead of
        # queuing — WAL mode lets readers/writers coexist, and a busy
        # timeout makes a writer wait for a lock instead of failing
        # immediately. Postgres needs none of this (real MVCC).
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

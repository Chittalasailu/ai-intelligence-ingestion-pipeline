"""SQLite-backed checkpointing so a crawl can be killed (or crash) and
resumed without re-fetching everything or re-emitting duplicates.

One `CheckpointStore` per pipeline run persists to a single .sqlite file;
each logical source gets its own namespace. Safe to call from many
concurrent asyncio tasks because writes go through a single dedicated
thread via `asyncio.to_thread` and SQLite's own locking.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS seen_items (
                    namespace TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, item_id)
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS pagination_state (
                    namespace TEXT PRIMARY KEY,
                    cursor TEXT,
                    updated_at TEXT
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS content_hashes (
                    content_hash TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL
                )"""
            )

    # -- sync primitives (used inside asyncio.to_thread) --
    def _has_seen_sync(self, namespace: str, item_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM seen_items WHERE namespace=? AND item_id=?", (namespace, item_id))
            return cur.fetchone() is not None

    def _mark_seen_sync(self, namespace: str, item_id: str, seen_at: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO seen_items (namespace, item_id, seen_at) VALUES (?, ?, ?)",
                (namespace, item_id, seen_at),
            )

    def _all_seen_sync(self, namespace: str) -> set[str]:
        with self._cursor() as cur:
            cur.execute("SELECT item_id FROM seen_items WHERE namespace=?", (namespace,))
            return {row[0] for row in cur.fetchall()}

    def _get_cursor_sync(self, namespace: str) -> Optional[str]:
        with self._cursor() as cur:
            cur.execute("SELECT cursor FROM pagination_state WHERE namespace=?", (namespace,))
            row = cur.fetchone()
            return row[0] if row else None

    def _set_cursor_sync(self, namespace: str, cursor: str, updated_at: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO pagination_state (namespace, cursor, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(namespace) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at""",
                (namespace, cursor, updated_at),
            )

    def _has_content_hash_sync(self, content_hash: str) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM content_hashes WHERE content_hash=?", (content_hash,))
            return cur.fetchone() is not None

    def _mark_content_hash_sync(self, content_hash: str, namespace: str, first_seen_at: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO content_hashes (content_hash, namespace, first_seen_at) VALUES (?, ?, ?)",
                (content_hash, namespace, first_seen_at),
            )

    # -- async-facing API --
    async def has_seen(self, namespace: str, item_id: str) -> bool:
        return await asyncio.to_thread(self._has_seen_sync, namespace, item_id)

    async def mark_seen(self, namespace: str, item_id: str, seen_at: str) -> None:
        await asyncio.to_thread(self._mark_seen_sync, namespace, item_id, seen_at)

    async def all_seen(self, namespace: str) -> set[str]:
        return await asyncio.to_thread(self._all_seen_sync, namespace)

    async def get_cursor(self, namespace: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_cursor_sync, namespace)

    async def set_cursor(self, namespace: str, cursor: str, updated_at: str) -> None:
        await asyncio.to_thread(self._set_cursor_sync, namespace, cursor, updated_at)

    async def has_content_hash(self, content_hash: str) -> bool:
        return await asyncio.to_thread(self._has_content_hash_sync, content_hash)

    async def mark_content_hash(self, content_hash: str, namespace: str, first_seen_at: str) -> None:
        await asyncio.to_thread(self._mark_content_hash_sync, content_hash, namespace, first_seen_at)

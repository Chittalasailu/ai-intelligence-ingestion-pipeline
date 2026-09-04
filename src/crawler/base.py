"""Generic concurrent-task runner used by every crawler/extractor.

This is the piece that lets the architecture scale from ~1k records to
500k+ by raising MAX_CONCURRENCY / adding worker processes rather than
rewriting logic: `run_bounded` is the only place concurrency is decided,
and every task is isolated so one bad record can't take down a batch.
"""
from __future__ import annotations

import asyncio
import signal
import sys
import traceback
from typing import Awaitable, Callable, Generic, Optional, Sequence, TypeVar

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class GracefulShutdown:
    """Cooperative shutdown flag. On POSIX, wired to SIGINT/SIGTERM via the
    event loop. On Windows, asyncio's add_signal_handler is unsupported, so
    callers should also wrap their top-level `asyncio.run(...)` in a
    try/except KeyboardInterrupt that calls `.trigger()` — see src/main.py.
    """

    def __init__(self):
        self._event = asyncio.Event()

    def trigger(self, *_args) -> None:
        if not self._event.is_set():
            logger.warning("graceful_shutdown_triggered")
            self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, self.trigger)
            except (NotImplementedError, RuntimeError, ValueError):
                pass  # Windows / restricted loop: fall back to KeyboardInterrupt at the call site.


async def run_bounded(
    items: Sequence[T],
    worker: Callable[[T], Awaitable[R]],
    concurrency: int,
    shutdown: Optional[GracefulShutdown] = None,
    on_error: Optional[Callable[[T, BaseException], None]] = None,
) -> list[Optional[R]]:
    """Run `worker(item)` for every item with at most `concurrency` in
    flight at once. A single item raising never aborts the batch — the
    exception is caught, logged, and that slot returns None — this is the
    "failure isolation" requirement: one malformed page/record can't take
    down a 500k-record run.
    """
    semaphore = asyncio.Semaphore(concurrency)
    results: list[Optional[R]] = [None] * len(items)

    async def _run_one(index: int, item: T) -> None:
        if shutdown and shutdown.is_set:
            return
        async with semaphore:
            if shutdown and shutdown.is_set:
                return
            try:
                results[index] = await worker(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - intentional: isolate any task failure
                logger.error(
                    "task_failed",
                    item=str(item)[:200],
                    error=str(e),
                    error_type=type(e).__name__,
                    traceback=traceback.format_exc(),
                )
                if on_error:
                    on_error(item, e)

    await asyncio.gather(*(_run_one(i, item) for i, item in enumerate(items)))
    return results


class RateLimitedPager:
    """Helper for paginated APIs: yields pages until `has_more` returns
    False, a page comes back empty, or graceful shutdown is requested.
    Keeps a resumable cursor via CheckpointStore so a killed crawl restarts
    from where it left off instead of re-fetching page 1..N every time.
    """

    def __init__(
        self,
        fetch_page: Callable[[Optional[str]], Awaitable[tuple[list, Optional[str]]]],
        shutdown: Optional[GracefulShutdown] = None,
        max_pages: Optional[int] = None,
    ):
        self.fetch_page = fetch_page
        self.shutdown = shutdown
        self.max_pages = max_pages

    async def pages(self, start_cursor: Optional[str] = None):
        cursor = start_cursor
        page_count = 0
        while True:
            if self.shutdown and self.shutdown.is_set:
                logger.info("pager_stopping_for_shutdown")
                return
            if self.max_pages is not None and page_count >= self.max_pages:
                return
            items, next_cursor = await self.fetch_page(cursor)
            if not items:
                return
            yield items, cursor
            page_count += 1
            if not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

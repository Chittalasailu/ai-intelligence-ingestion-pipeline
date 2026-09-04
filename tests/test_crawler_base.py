import asyncio

from src.crawler.base import GracefulShutdown, run_bounded


async def test_run_bounded_processes_all_items():
    items = list(range(20))

    async def worker(x):
        return x * 2

    results = await run_bounded(items, worker, concurrency=5)
    assert results == [x * 2 for x in items]


async def test_run_bounded_respects_concurrency_limit():
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def worker(x):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return x

    await run_bounded(list(range(30)), worker, concurrency=4)
    assert max_in_flight <= 4


async def test_run_bounded_isolates_failures():
    async def worker(x):
        if x == 5:
            raise ValueError("boom")
        return x

    errors = []
    results = await run_bounded(list(range(10)), worker, concurrency=3, on_error=lambda item, exc: errors.append(item))

    assert results[5] is None  # the failed item's slot is None, not a crash
    assert results[0] == 0 and results[9] == 9  # everything else still completed
    assert errors == [5]


async def test_run_bounded_empty_list():
    async def worker(x):
        return x

    assert await run_bounded([], worker, concurrency=5) == []


async def test_graceful_shutdown_stops_new_work():
    shutdown = GracefulShutdown()
    started = []

    async def worker(x):
        started.append(x)
        if x == 3:
            shutdown.trigger()
        await asyncio.sleep(0.01)
        return x

    await run_bounded(list(range(20)), worker, concurrency=1, shutdown=shutdown)
    # With concurrency=1, items run strictly in order, so shutdown after
    # item 3 must prevent at least the final item from ever starting.
    assert 19 not in started


def test_graceful_shutdown_is_idempotent():
    shutdown = GracefulShutdown()
    assert shutdown.is_set is False
    shutdown.trigger()
    shutdown.trigger()
    assert shutdown.is_set is True

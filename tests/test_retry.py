import asyncio

import pytest

from src.utils.retry import BackoffConfig, RateLimitError, RetryableError, compute_delay, retry_async


def test_compute_delay_uses_retry_after_when_given():
    cfg = BackoffConfig(base_seconds=1.0, max_seconds=60.0, jitter_seconds=0.0)
    delay = compute_delay(attempt=0, cfg=cfg, retry_after=10.0)
    assert delay == 10.0


def test_compute_delay_exponential_growth():
    cfg = BackoffConfig(base_seconds=1.0, max_seconds=1000.0, jitter_seconds=0.0)
    d0 = compute_delay(0, cfg)
    d1 = compute_delay(1, cfg)
    d2 = compute_delay(2, cfg)
    assert d0 == 1.0
    assert d1 == 2.0
    assert d2 == 4.0


def test_compute_delay_caps_at_max_seconds():
    cfg = BackoffConfig(base_seconds=1.0, max_seconds=5.0, jitter_seconds=0.0)
    delay = compute_delay(attempt=10, cfg=cfg)
    assert delay == 5.0


def test_compute_delay_adds_jitter_within_bounds():
    cfg = BackoffConfig(base_seconds=1.0, max_seconds=60.0, jitter_seconds=1.0)
    delays = {compute_delay(0, cfg) for _ in range(20)}
    assert all(1.0 <= d <= 2.0 for d in delays)
    assert len(delays) > 1  # jitter actually varies across calls


async def test_retry_async_succeeds_on_first_try():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    cfg = BackoffConfig(max_retries=3, base_seconds=0.001, jitter_seconds=0.0)
    result = await retry_async(fn, cfg)
    assert result == "ok"
    assert len(calls) == 1


async def test_retry_async_retries_then_succeeds():
    attempts = {"n": 0}

    async def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RetryableError("transient")
        return "recovered"

    cfg = BackoffConfig(max_retries=5, base_seconds=0.001, jitter_seconds=0.0)
    result = await retry_async(fn, cfg)
    assert result == "recovered"
    assert attempts["n"] == 3


async def test_retry_async_exhausts_and_raises():
    async def fn():
        raise RetryableError("always fails")

    cfg = BackoffConfig(max_retries=2, base_seconds=0.001, jitter_seconds=0.0)
    with pytest.raises(RetryableError):
        await retry_async(fn, cfg)


async def test_retry_async_calls_on_retry_callback_with_delay():
    seen = []

    async def fn():
        if len(seen) < 1:
            raise RateLimitError("429", retry_after=0.001)
        return "done"

    cfg = BackoffConfig(max_retries=3, base_seconds=0.001, jitter_seconds=0.0)
    await retry_async(fn, cfg, on_retry=lambda attempt, exc, delay: seen.append((attempt, delay)))
    assert len(seen) == 1
    assert seen[0][1] == 0.001  # honored retry_after, not the exponential default


async def test_retry_async_non_retryable_exception_propagates_immediately():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ValueError("not retryable")

    cfg = BackoffConfig(max_retries=5, base_seconds=0.001)
    with pytest.raises(ValueError):
        await retry_async(fn, cfg)
    assert calls["n"] == 1

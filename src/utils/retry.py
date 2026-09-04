"""Generic async retry with exponential backoff + jitter.

Used by both the HTTP client (crawler) and the LLM provider layer so 429/413
handling behaves identically everywhere instead of being reimplemented per
call site.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class RetryableError(Exception):
    """Raised by callables passed to `retry_async` to signal a transient
    failure worth retrying. `retry_after` (seconds), if set, overrides the
    computed backoff delay (e.g. from a Retry-After header on a 429).
    """

    def __init__(self, message: str, retry_after: Optional[float] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


class PayloadTooLargeError(RetryableError):
    """Raised specifically for HTTP 413 so callers can shrink the payload
    before the next attempt instead of just waiting and resending the same
    oversized request.
    """


class RateLimitError(RetryableError):
    """Raised specifically for HTTP 429."""


@dataclass
class BackoffConfig:
    max_retries: int = 4
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    jitter_seconds: float = 0.5


def compute_delay(attempt: int, cfg: BackoffConfig, retry_after: Optional[float] = None) -> float:
    if retry_after is not None:
        return min(retry_after, cfg.max_seconds) + random.uniform(0, cfg.jitter_seconds)
    exp = min(cfg.base_seconds * (2 ** attempt), cfg.max_seconds)
    return exp + random.uniform(0, cfg.jitter_seconds)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    cfg: BackoffConfig,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    retryable_exceptions: tuple[type[Exception], ...] = (RetryableError, asyncio.TimeoutError, ConnectionError),
) -> T:
    """Call `fn()` up to cfg.max_retries+1 times. Re-raises the last error
    if every attempt is exhausted.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn()
        except retryable_exceptions as e:  # type: ignore[misc]
            last_exc = e
            if attempt >= cfg.max_retries:
                break
            retry_after = getattr(e, "retry_after", None)
            delay = compute_delay(attempt, cfg, retry_after)
            if on_retry:
                on_retry(attempt, e, delay)
            logger.info("retrying", attempt=attempt + 1, max_retries=cfg.max_retries, delay_seconds=round(delay, 2), error=str(e))
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc

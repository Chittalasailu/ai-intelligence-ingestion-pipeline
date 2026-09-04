"""Shared async HTTP client: connection pooling, global + per-host
concurrency limits, timeouts, and retry/backoff on transient failures.

This is the single place network calls flow through for the crawler layer,
so scaling from 1k to 500k records is a config change (MAX_CONCURRENCY,
PER_HOST_CONCURRENCY) rather than a rewrite.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from types import TracebackType
from typing import Any, Optional

import aiohttp

from src.utils.logging_setup import get_logger
from src.utils.retry import BackoffConfig, RateLimitError, RetryableError, retry_async

logger = get_logger(__name__)


class AsyncHttpClient:
    def __init__(
        self,
        max_concurrency: int = 25,
        per_host_concurrency: int = 5,
        timeout_seconds: float = 20.0,
        user_agent: str = "FrontierAtlasIntelligenceBot/1.0",
        backoff: Optional[BackoffConfig] = None,
    ):
        self.max_concurrency = max_concurrency
        self.per_host_concurrency = per_host_concurrency
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.backoff = backoff or BackoffConfig()

        self._session: Optional[aiohttp.ClientSession] = None
        self._global_sem = asyncio.Semaphore(max_concurrency)
        self._host_sems: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_host_concurrency)
        )

    async def __aenter__(self) -> "AsyncHttpClient":
        connector = aiohttp.TCPConnector(limit=self.max_concurrency, limit_per_host=self.per_host_concurrency)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=timeout, headers={"User-Agent": self.user_agent}
        )
        return self

    async def __aexit__(
        self, exc_type: Optional[type[BaseException]], exc: Optional[BaseException], tb: Optional[TracebackType]
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _host_sem(self, url: str) -> asyncio.Semaphore:
        host = aiohttp.client.URL(url).host or "unknown"
        return self._host_sems[host]

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        expect_json: bool = False,
        max_retries_override: Optional[int] = None,
        **kwargs: Any,
    ) -> tuple[int, Any, dict[str, str]]:
        """Returns (status_code, body, headers). Body is parsed JSON if
        expect_json else raw text. Retries transient failures (timeouts,
        connection errors, 429, 5xx); 404/400/401/403 fail fast (not retryable).
        """
        assert self._session is not None, "use `async with AsyncHttpClient() as client`"
        cfg = self.backoff
        if max_retries_override is not None:
            cfg = BackoffConfig(
                max_retries=max_retries_override,
                base_seconds=cfg.base_seconds,
                max_seconds=cfg.max_seconds,
                jitter_seconds=cfg.jitter_seconds,
            )
        host_sem = self._host_sem(url)

        async def _do_request():
            async with self._global_sem, host_sem:
                async with self._session.request(method, url, **kwargs) as resp:
                    headers = dict(resp.headers)
                    if resp.status == 429:
                        retry_after = _parse_retry_after(headers.get("Retry-After"))
                        raise RateLimitError(f"429 from {url}", retry_after=retry_after, status_code=429)
                    if resp.status >= 500:
                        raise RetryableError(f"{resp.status} from {url}", status_code=resp.status)
                    if resp.status >= 400:
                        # Not retryable: bad request / not found / forbidden.
                        body = await resp.text()
                        return resp.status, body, headers
                    body = await resp.json(content_type=None) if expect_json else await resp.text()
                    return resp.status, body, headers

        def _on_retry(attempt: int, exc: Exception, delay: float) -> None:
            if isinstance(exc, RateLimitError):
                logger.warning("http_429_retry", url=url, attempt=attempt + 1, delay=round(delay, 2))

        try:
            return await retry_async(_do_request, cfg, on_retry=_on_retry)
        except RetryableError as e:
            logger.error("http_request_failed_after_retries", url=url, error=str(e))
            return e.status_code or 599, None, {}
        except asyncio.TimeoutError:
            logger.error("http_timeout_after_retries", url=url)
            return 598, None, {}
        except (aiohttp.ClientError, ConnectionError, OSError) as e:
            # DNS failures, connection resets, TLS errors, etc. — anything
            # retry_async already retried and gave up on. fetch()'s contract
            # is "always returns a (status, body, headers) tuple", so a dead
            # host must degrade to a failure code here, not raise and take
            # down whatever run_bounded task called us.
            logger.error("http_connection_failed_after_retries", url=url, error=str(e))
            return 599, None, {}


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

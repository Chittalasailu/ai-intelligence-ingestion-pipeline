"""Multi-tier LLM orchestration: Gemini -> Groq -> DeepSeek (configurable
order), with intelligent chunking, 429/413 handling, and structured-output
validation. This is the only entry point pipelines use for LLM extraction —
they never talk to a provider directly.

413 strategy (never just truncate blindly):
  1. clean_html + chunk_text already keep each request under a conservative
     token budget before the first attempt.
  2. if a provider *still* 413s (its real limit turned out stricter than
     assumed), shrink_for_413 halves the chunk budget and we retry that
     provider once more with smaller chunks.
  3. if it 413s again, we give up on that provider for this document and
     fall through to the next one in the chain — a provider with a smaller
     context window doesn't get to fail the whole extraction.

429 strategy: exponential backoff + jitter, honoring Retry-After when the
provider sends one, up to max_429_retries — then fall through to the next
provider rather than continuing to hammer a throttled one.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from src.llm.base import LLMProvider, LLMResult, LLMUsage, ProviderHTTPError
from src.schemas.validation import QualityStats
from src.utils.chunking import chunk_text, clean_html, shrink_for_413
from src.utils.logging_setup import get_logger
from src.utils.retry import BackoffConfig, PayloadTooLargeError, RateLimitError, retry_async

logger = get_logger(__name__)


@dataclass
class OrchestratorConfig:
    provider_order: list[str] = field(default_factory=lambda: ["gemini", "groq", "deepseek"])
    chunk_token_budget: int = 3500
    chunk_overlap_tokens: int = 200
    max_tokens_per_request: int = 2000
    max_429_retries: int = 5
    max_413_retries: int = 3
    request_timeout_seconds: float = 30.0


class LLMOrchestrator:
    def __init__(self, providers: dict[str, LLMProvider], config: OrchestratorConfig, stats: Optional[QualityStats] = None):
        self.providers = providers
        self.config = config
        self.stats = stats
        self._usage: dict[str, LLMUsage] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "LLMOrchestrator":
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_seconds)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session is not None:
            await self._session.close()

    @property
    def configured_providers(self) -> list[LLMProvider]:
        ordered = []
        for name in self.config.provider_order:
            p = self.providers.get(name)
            if p is not None and p.is_configured:
                ordered.append(p)
        return ordered

    def usage_report(self) -> dict[str, dict[str, int]]:
        return {name: {"prompt_tokens_est": u.prompt_tokens_est, "completion_tokens_est": u.completion_tokens_est} for name, u in self._usage.items()}

    def _track_usage(self, provider_name: str, usage: LLMUsage) -> None:
        existing = self._usage.setdefault(provider_name, LLMUsage())
        existing.prompt_tokens_est += usage.prompt_tokens_est
        existing.completion_tokens_est += usage.completion_tokens_est

    async def _call_with_413_handling(self, provider: LLMProvider, system_prompt: str, content: str, token_budget: int) -> LLMResult:
        current_content = content
        current_budget = token_budget
        for attempt_413 in range(self.config.max_413_retries + 1):
            try:

                async def _do_call() -> LLMResult:
                    assert self._session is not None
                    try:
                        return await provider.call(self._session, system_prompt, current_content, self.config.max_tokens_per_request)
                    except ProviderHTTPError as e:
                        if e.status_code == 429:
                            if self.stats:
                                self.stats.incr("retries_429")
                            raise RateLimitError(str(e), retry_after=e.retry_after, status_code=429) from e
                        if e.status_code == 413:
                            raise PayloadTooLargeError(str(e), status_code=413) from e
                        raise

                backoff = BackoffConfig(max_retries=self.config.max_429_retries)
                result = await retry_async(_do_call, backoff, retryable_exceptions=(RateLimitError, asyncio.TimeoutError))
                return result
            except PayloadTooLargeError:
                if self.stats:
                    self.stats.incr("retries_413")
                if attempt_413 >= self.config.max_413_retries:
                    logger.error("413_retries_exhausted", provider=provider.name)
                    return LLMResult(success=False, provider=provider.name, error="413_exhausted", status_code=413)
                current_budget = max(current_budget // 2, 500)
                shrunk = shrink_for_413([], current_content, current_budget * 2, self.config.chunk_overlap_tokens)
                current_content = shrunk[0].text if shrunk else current_content[: current_budget * 4]
                logger.warning("413_shrinking_and_retrying", provider=provider.name, new_token_budget=current_budget, attempt=attempt_413 + 1)
            except RateLimitError as e:
                logger.error("429_retries_exhausted", provider=provider.name, error=str(e))
                return LLMResult(success=False, provider=provider.name, error="429_exhausted", status_code=429)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error("provider_transport_error", provider=provider.name, error=str(e))
                return LLMResult(success=False, provider=provider.name, error=f"transport_error: {e}")
        return LLMResult(success=False, provider=provider.name, error="413_exhausted")

    async def extract_structured(
        self,
        system_prompt: str,
        raw_text_or_html: str,
        target_keys: Optional[list[str]] = None,
        is_html: bool = False,
    ) -> LLMResult:
        """Runs the full chain (chunk -> provider fallback) and returns the
        first result that is a successful, schema-plausible JSON object.
        `target_keys`, if given, lets us stop early once a chunk's result
        already has every key non-null instead of processing every chunk.
        """
        text = clean_html(raw_text_or_html) if is_html else raw_text_or_html
        chunks = chunk_text(text, token_budget=self.config.chunk_token_budget, overlap_tokens=self.config.chunk_overlap_tokens)
        if not chunks:
            if self.stats:
                self.stats.incr("llm_failures")
            return LLMResult(success=False, provider="none", error="empty_content")

        providers = self.configured_providers
        if not providers:
            if self.stats:
                self.stats.incr("llm_failures")
            return LLMResult(success=False, provider="none", error="no_providers_configured")

        last_result: Optional[LLMResult] = None
        for chunk in chunks:
            for provider in providers:
                start = time.monotonic()
                result = await self._call_with_413_handling(provider, system_prompt, chunk.text, self.config.chunk_token_budget)
                result.latency_ms = result.latency_ms or (time.monotonic() - start) * 1000
                self._track_usage(provider.name, result.usage)
                last_result = result

                if result.success and result.data:
                    if not target_keys or all(result.data.get(k) not in (None, "") for k in target_keys):
                        if self.stats:
                            self.stats.incr("llm_successes")
                        return result
                    break  # this chunk done; try next chunk to fill remaining keys, starting from tier 1 again
                else:
                    logger.warning("llm_provider_failed_falling_back", provider=provider.name, error=result.error)
                    continue

        final = last_result or LLMResult(success=False, provider="none", error="all_providers_exhausted")
        if self.stats:
            self.stats.incr("llm_successes" if final.success else "llm_failures")
        return final

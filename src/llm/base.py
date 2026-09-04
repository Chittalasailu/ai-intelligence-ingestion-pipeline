"""Provider-agnostic interface every LLM tier implements. The orchestrator
only ever talks to this interface, so adding a 4th provider means writing
one small adapter class, not touching orchestration logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import aiohttp


@dataclass
class LLMUsage:
    prompt_tokens_est: int = 0
    completion_tokens_est: int = 0

    @property
    def total_tokens_est(self) -> int:
        return self.prompt_tokens_est + self.completion_tokens_est


@dataclass
class LLMResult:
    success: bool
    provider: str
    raw_text: Optional[str] = None
    data: Optional[dict] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: float = 0.0
    usage: LLMUsage = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.usage is None:
            self.usage = LLMUsage()


class ProviderHTTPError(Exception):
    def __init__(self, message: str, status_code: int, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @abstractmethod
    async def call(self, session: aiohttp.ClientSession, system_prompt: str, user_content: str, max_tokens: int) -> LLMResult:
        """Issue one request. Must raise ProviderHTTPError(429) or
        ProviderHTTPError(413) for those specific statuses so the
        orchestrator's retry/chunk-shrink logic can react correctly, rather
        than swallowing them into a generic failure.
        """
        raise NotImplementedError

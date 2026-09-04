"""Concrete LLM provider adapters. Each talks to its provider's REST API
directly over aiohttp (no vendor SDKs) so the orchestrator's timeout/retry/
session pooling applies uniformly across all three.

All three are OpenAI-style-compatible or close enough that the differences
are isolated here; the orchestrator never branches on provider name.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import aiohttp

from src.llm.base import LLMProvider, LLMResult, LLMUsage, ProviderHTTPError


def _extract_json_object(text: str) -> Optional[dict]:
    """Providers sometimes wrap JSON in prose or markdown fences even when
    asked not to. Try strict parse first, then salvage the first balanced
    {...} block.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


async def _raise_for_status(resp: aiohttp.ClientResponse, provider: str) -> None:
    if resp.status == 429:
        retry_after = resp.headers.get("Retry-After")
        raise ProviderHTTPError(f"{provider} rate limited", 429, retry_after=float(retry_after) if retry_after else None)
    if resp.status == 413:
        raise ProviderHTTPError(f"{provider} payload too large", 413)
    if resp.status >= 400:
        body = await resp.text()
        raise ProviderHTTPError(f"{provider} error {resp.status}: {body[:300]}", resp.status)


class GeminiProvider(LLMProvider):
    name = "gemini"

    async def call(self, session: aiohttp.ClientSession, system_prompt: str, user_content: str, max_tokens: int) -> LLMResult:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_content}"}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }
        start = time.monotonic()
        async with session.post(url, json=payload) as resp:
            await _raise_for_status(resp, self.name)
            body = await resp.json()
        latency_ms = (time.monotonic() - start) * 1000

        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return LLMResult(success=False, provider=self.name, error="unexpected_response_shape", raw_text=str(body)[:500], latency_ms=latency_ms)

        data = _extract_json_object(text)
        usage_meta = body.get("usageMetadata", {})
        usage = LLMUsage(
            prompt_tokens_est=usage_meta.get("promptTokenCount", 0),
            completion_tokens_est=usage_meta.get("candidatesTokenCount", 0),
        )
        if data is None:
            return LLMResult(success=False, provider=self.name, raw_text=text, error="json_parse_failed", latency_ms=latency_ms, usage=usage)
        return LLMResult(success=True, provider=self.name, raw_text=text, data=data, latency_ms=latency_ms, usage=usage)


class _OpenAICompatibleProvider(LLMProvider):
    """Shared implementation for Groq and DeepSeek, which both expose an
    OpenAI-compatible /chat/completions endpoint.
    """

    base_url: str = ""

    async def call(self, session: aiohttp.ClientSession, system_prompt: str, user_content: str, max_tokens: int) -> LLMResult:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        start = time.monotonic()
        async with session.post(self.base_url, json=payload, headers=headers) as resp:
            await _raise_for_status(resp, self.name)
            body = await resp.json()
        latency_ms = (time.monotonic() - start) * 1000

        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return LLMResult(success=False, provider=self.name, error="unexpected_response_shape", raw_text=str(body)[:500], latency_ms=latency_ms)

        data = _extract_json_object(text)
        usage_raw = body.get("usage", {})
        usage = LLMUsage(
            prompt_tokens_est=usage_raw.get("prompt_tokens", 0),
            completion_tokens_est=usage_raw.get("completion_tokens", 0),
        )
        if data is None:
            return LLMResult(success=False, provider=self.name, raw_text=text, error="json_parse_failed", latency_ms=latency_ms, usage=usage)
        return LLMResult(success=True, provider=self.name, raw_text=text, data=data, latency_ms=latency_ms, usage=usage)


class GroqProvider(_OpenAICompatibleProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1/chat/completions"


class DeepSeekProvider(_OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com/chat/completions"

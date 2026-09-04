import aiohttp
import pytest
from aioresponses import aioresponses

from src.llm.base import LLMProvider, LLMResult, LLMUsage, ProviderHTTPError
from src.llm.orchestrator import LLMOrchestrator, OrchestratorConfig
from src.llm.providers import GroqProvider, _extract_json_object
from src.schemas.validation import QualityStats


class FakeProvider(LLMProvider):
    """Test double: scripted sequence of outcomes per call, so orchestrator
    fallback/retry logic can be tested without real network calls.
    """

    def __init__(self, name: str, outcomes: list):
        super().__init__(api_key="fake-key", model="fake-model")
        self.name = name
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def call(self, session, system_prompt, user_content, max_tokens) -> LLMResult:
        self.call_count += 1
        assert self._outcomes, f"{self.name} called more times than the test scripted outcomes for"
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _success(data: dict) -> LLMResult:
    return LLMResult(success=True, provider="fake", data=data, raw_text=str(data))


async def test_extract_structured_uses_first_successful_provider():
    gemini = FakeProvider("gemini", [_success({"role_family": "Engineering"})])
    groq = FakeProvider("groq", [_success({"role_family": "Sales"})])
    orch = LLMOrchestrator({"gemini": gemini, "groq": groq}, OrchestratorConfig(provider_order=["gemini", "groq"]), QualityStats())

    async with orch:
        result = await orch.extract_structured("system", "some job text", target_keys=["role_family"])

    assert result.success is True
    assert result.data["role_family"] == "Engineering"
    assert gemini.call_count == 1
    assert groq.call_count == 0  # never needed — first provider already succeeded


async def test_extract_structured_falls_back_when_first_provider_unconfigured():
    gemini = FakeProvider("gemini", [])
    gemini.api_key = ""  # not configured -> orchestrator must skip it entirely
    groq = FakeProvider("groq", [_success({"role_family": "Design"})])
    orch = LLMOrchestrator({"gemini": gemini, "groq": groq}, OrchestratorConfig(provider_order=["gemini", "groq"]), QualityStats())

    async with orch:
        result = await orch.extract_structured("system", "text", target_keys=["role_family"])

    assert result.success is True
    assert result.provider == "fake"
    assert gemini.call_count == 0
    assert groq.call_count == 1


async def test_extract_structured_falls_back_on_429_exhaustion():
    gemini = FakeProvider("gemini", [ProviderHTTPError("rate limited", 429)] * 10)
    deepseek = FakeProvider("deepseek", [_success({"role_family": "Product"})])
    cfg = OrchestratorConfig(provider_order=["gemini", "deepseek"], max_429_retries=2)
    stats = QualityStats()
    orch = LLMOrchestrator({"gemini": gemini, "deepseek": deepseek}, cfg, stats)

    async with orch:
        result = await orch.extract_structured("system", "text", target_keys=["role_family"])

    assert result.success is True
    assert result.data["role_family"] == "Product"
    assert stats.retries_429 > 0
    assert gemini.call_count == cfg.max_429_retries + 1  # exhausted its retries, then gave up


async def test_extract_structured_falls_back_on_413_exhaustion():
    gemini = FakeProvider("gemini", [ProviderHTTPError("too large", 413)] * 10)
    groq = FakeProvider("groq", [_success({"headline": "ok"})])
    cfg = OrchestratorConfig(provider_order=["gemini", "groq"], max_413_retries=1)
    stats = QualityStats()
    orch = LLMOrchestrator({"gemini": gemini, "groq": groq}, cfg, stats)

    async with orch:
        result = await orch.extract_structured("system", "a" * 20000, target_keys=["headline"])

    assert result.success is True
    assert result.provider == "fake"
    assert stats.retries_413 > 0


async def test_extract_structured_no_providers_configured_returns_failure():
    gemini = FakeProvider("gemini", [])
    gemini.api_key = ""
    stats = QualityStats()
    orch = LLMOrchestrator({"gemini": gemini}, OrchestratorConfig(provider_order=["gemini"]), stats)

    async with orch:
        result = await orch.extract_structured("system", "text")

    assert result.success is False
    assert result.error == "no_providers_configured"
    assert stats.llm_failures == 1


async def test_extract_structured_empty_content_short_circuits():
    gemini = FakeProvider("gemini", [_success({"x": 1})])
    orch = LLMOrchestrator({"gemini": gemini}, OrchestratorConfig(provider_order=["gemini"]), QualityStats())

    async with orch:
        result = await orch.extract_structured("system", "")

    assert result.success is False
    assert result.error == "empty_content"
    assert gemini.call_count == 0


def test_extract_json_object_handles_plain_json():
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_handles_markdown_fence():
    text = '```json\n{"a": 1, "b": "two"}\n```'
    assert _extract_json_object(text) == {"a": 1, "b": "two"}


def test_extract_json_object_handles_prose_wrapping():
    text = 'Sure! Here is the JSON you asked for: {"a": 1} — hope that helps.'
    assert _extract_json_object(text) == {"a": 1}


def test_extract_json_object_returns_none_for_garbage():
    assert _extract_json_object("no json here at all") is None


async def test_groq_provider_real_http_shape_via_mock():
    provider = GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile")
    body = {
        "choices": [{"message": {"content": '{"role_family": "Engineering"}'}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 8},
    }
    with aioresponses() as m:
        m.post("https://api.groq.com/openai/v1/chat/completions", payload=body, status=200)
        async with aiohttp.ClientSession() as session:
            result = await provider.call(session, "system", "user content", 100)

    assert result.success is True
    assert result.data == {"role_family": "Engineering"}
    assert result.usage.prompt_tokens_est == 120


async def test_groq_provider_raises_on_429_for_orchestrator_to_catch():
    provider = GroqProvider(api_key="test-key", model="llama-3.3-70b-versatile")
    with aioresponses() as m:
        m.post("https://api.groq.com/openai/v1/chat/completions", status=429, headers={"Retry-After": "2"})
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ProviderHTTPError) as exc_info:
                await provider.call(session, "system", "user content", 100)

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == 2.0

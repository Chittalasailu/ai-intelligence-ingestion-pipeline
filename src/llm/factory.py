"""Wires up providers + orchestrator config from Settings. The one place
that knows about env-var-backed API keys; everything downstream just gets
an LLMOrchestrator.
"""
from __future__ import annotations

from typing import Optional

from src.llm.orchestrator import LLMOrchestrator, OrchestratorConfig
from src.llm.providers import DeepSeekProvider, GeminiProvider, GroqProvider
from src.schemas.validation import QualityStats
from src.utils.config import Settings


def build_orchestrator(settings: Settings, stats: Optional[QualityStats] = None) -> LLMOrchestrator:
    llm_cfg = settings.raw_settings.get("llm", {})
    providers = {
        "gemini": GeminiProvider(settings.gemini_api_key, settings.gemini_model),
        "groq": GroqProvider(settings.groq_api_key, settings.groq_model),
        "deepseek": DeepSeekProvider(settings.deepseek_api_key, settings.deepseek_model),
    }
    config = OrchestratorConfig(
        provider_order=llm_cfg.get("provider_order", ["gemini", "groq", "deepseek"]),
        chunk_token_budget=llm_cfg.get("chunk_token_budget", 3500),
        chunk_overlap_tokens=llm_cfg.get("chunk_overlap_tokens", 200),
        max_tokens_per_request=llm_cfg.get("max_tokens_per_request", 2000),
        max_429_retries=llm_cfg.get("max_429_retries", 5),
        max_413_retries=llm_cfg.get("max_413_retries", 3),
    )
    return LLMOrchestrator(providers=providers, config=config, stats=stats)

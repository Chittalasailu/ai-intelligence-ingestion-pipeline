"""Central config loader: merges config/settings.yaml, config/sources.yaml,
and environment variables (.env) into one object. Nothing else in the
codebase should call `os.getenv` directly for these values — this is the
single source of truth so behavior stays consistent across pipelines.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Settings:
    raw_settings: dict[str, Any] = field(default_factory=dict)
    raw_sources: dict[str, Any] = field(default_factory=dict)

    # secrets / env
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    github_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/pipeline.db"
    google_service_account_file: str = "./service-account.json"
    google_sheet_id: str = ""
    log_level: str = "INFO"

    max_concurrency: int = 25
    per_host_concurrency: int = 5
    request_timeout_seconds: float = 20.0
    max_retries: int = 4
    freshness_window_hours: int = 24

    def source_group(self, name: str) -> list[dict[str, Any]]:
        return self.raw_sources.get(name, []) or []


def load_settings(root: Path | None = None) -> Settings:
    root = root or ROOT
    load_dotenv(root / ".env", override=False)

    raw_settings = _load_yaml(root / "config" / "settings.yaml")
    raw_sources = _load_yaml(root / "config" / "sources.yaml")

    crawler_cfg = raw_settings.get("crawler", {})
    freshness_cfg = raw_settings.get("freshness", {})

    return Settings(
        raw_settings=raw_settings,
        raw_sources=raw_sources,
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        database_url=os.getenv("DATABASE_URL", raw_settings.get("storage", {}).get("default_url", "sqlite+aiosqlite:///./data/pipeline.db")),
        google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "./service-account.json"),
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_concurrency=int(os.getenv("MAX_CONCURRENCY", crawler_cfg.get("max_concurrency", 25))),
        per_host_concurrency=int(os.getenv("PER_HOST_CONCURRENCY", crawler_cfg.get("per_host_concurrency", 5))),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", crawler_cfg.get("request_timeout_seconds", 20.0))),
        max_retries=int(os.getenv("MAX_RETRIES", crawler_cfg.get("max_retries", 4))),
        freshness_window_hours=int(os.getenv("FRESHNESS_WINDOW_HOURS", freshness_cfg.get("window_hours", 24))),
    )

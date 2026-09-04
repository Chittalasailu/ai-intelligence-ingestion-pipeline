"""Structured logging setup. One call to configure_logging() at process start;
every module then gets a bound structlog logger via get_logger(__name__).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog

_CONFIGURED = False


def configure_logging(log_dir: Path | None = None, level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_level = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "pipeline.jsonl", encoding="utf-8"))

    logging.basicConfig(level=log_level, format="%(message)s", handlers=handlers)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str):
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)

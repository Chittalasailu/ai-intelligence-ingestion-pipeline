"""Validation gate: every record passes through here before storage/export.

Nothing downstream should ever see a record that hasn't cleared this module.
Rejections are logged with a reason, not silently dropped, so quality
statistics stay honest and debuggable.
"""
from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class QualityStats:
    """Running counters for one pipeline run. Thread/task-safe via a lock."""

    records_discovered: int = 0
    records_fetched: int = 0
    records_parsed: int = 0
    records_validated: int = 0
    records_rejected: int = 0
    duplicates_removed: int = 0
    llm_successes: int = 0
    llm_failures: int = 0
    retries_429: int = 0
    retries_413: int = 0
    freshness_failures: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def incr(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field_name, getattr(self, field_name) + amount)

    def as_dict(self) -> dict[str, int]:
        # NOT dataclasses.asdict(self) — it deep-copies every field including
        # `_lock`, and a threading.Lock can't be pickled/deepcopied.
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_lock"}


class RejectionLog:
    """Append-only JSONL log of rejected records, one file per pipeline run."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def reject(self, record_type: str, reason: str, raw: Any, source_url: Optional[str] = None) -> None:
        entry = {
            "record_type": record_type,
            "reason": reason,
            "source_url": source_url,
            "raw_preview": _safe_preview(raw),
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        logger.warning("record_rejected", record_type=record_type, reason=reason, source_url=source_url)


def _safe_preview(raw: Any, limit: int = 500) -> str:
    try:
        s = json.dumps(raw, default=str) if not isinstance(raw, str) else raw
    except Exception:
        s = str(raw)
    return s[:limit]


def validate_record(
    model_cls: type[BaseModel],
    payload: dict,
    stats: QualityStats,
    rejection_log: RejectionLog,
    record_type: str,
    source_url: Optional[str] = None,
) -> Optional[BaseModel]:
    """Validate `payload` against `model_cls`. Returns the parsed model or
    None (and logs+counts the rejection) if it fails schema validation.
    """
    try:
        instance = model_cls.model_validate(payload)
        stats.incr("records_validated")
        return instance
    except ValidationError as e:
        stats.incr("records_rejected")
        rejection_log.reject(record_type, f"schema_validation_error: {e.errors()[:3]}", payload, source_url)
        return None


def is_valid_github_evidence(url: Optional[str], evidence_text: Optional[str]) -> bool:
    """A GitHub URL is only trustworthy if it was found verbatim in source
    text (author-declared) — never inferred from title similarity. Callers
    pass the exact snippet the URL was extracted from as `evidence_text`.
    """
    if not url or not evidence_text:
        return False
    return url in evidence_text

"""Content-hash dedup: catches duplicates that arrive under different URLs
or IDs (e.g. a wire story syndicated to two sites, or a job cross-posted to
two boards) which URL/id-based checkpointing alone would miss.
"""
from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip().lower()


def content_hash(*parts: str) -> str:
    joined = "||".join(normalize_for_hash(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()

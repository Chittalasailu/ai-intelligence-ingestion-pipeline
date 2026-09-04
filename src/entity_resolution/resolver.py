"""Entity resolution pipeline: raw name -> canonical name.

Resolution order (cheapest/most-certain first):
  1. exact match on normalized string against known canonical names
  2. exact match on normalized string against the alias table
  3. fuzzy match (rapidfuzz token_sort_ratio) against canonical+alias corpus
  4. no match above threshold -> treat the normalized input itself as a new
     canonical entity (so it can still be merged with future duplicates of
     itself, without silently gluing it onto an unrelated existing one)

Every resolution — including "new canonical" — is written to the mapping
log with its method and confidence, so the log is a complete, auditable
raw-to-canonical trail rather than only recording the interesting cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from rapidfuzz import fuzz, process

from src.entity_resolution.normalizer import normalize_name
from src.entity_resolution.seed_data import SEED_STARTUPS
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class ResolutionResult:
    canonical_name: str
    method: str  # exact | alias | fuzzy | unmatched-new
    confidence: float


class EntityResolver:
    def __init__(
        self,
        seed: Optional[dict[str, list[str]]] = None,
        fuzzy_threshold: float = 97.0,
        review_threshold: float = 80.0,
    ):
        # 97, not 90: measured against this project's own ~2,450 real
        # resolutions, every fuzzy match rapidfuzz's token_sort_ratio ever
        # produced at threshold 90 was a false merge of two genuinely
        # different companies — 8 for 8, including "Cair Health"/"Caire
        # Health" at 95.65%, "Shape"/"Shaped"/"Sharpe" and "Sierra"/"Serra"
        # at 90.9%, and four more real pairs (Aluna/Alguna, Besimple AI/
        # Simple AI, Lever/Clever, Tella/Trella) — see
        # tests/test_entity_resolution.py and docs/LIMITATIONS.md for the
        # full list. Zero were legitimate typo catches. That's a precision
        # problem, not a tuning nuance: a short company name plus one
        # inserted/changed character routinely still scores 90-96% on
        # generic string similarity, because the metric has no notion that
        # "Lever" and "Clever" are unrelated businesses. Given the
        # assignment's own explicit cost asymmetry (an incorrect merge
        # corrupts data; a missed merge just leaves two records slightly
        # less consolidated), fuzzy matching now only auto-merges at
        # near-identity confidence. The 55-entity seed+alias table, not
        # fuzzy matching, is what actually carries the "OpenAI, Inc." /
        # "Open AI" canonicalization requirement — see the exact/alias
        # tests below, all of which still pass at this threshold.
        self.seed = seed if seed is not None else SEED_STARTUPS
        self.fuzzy_threshold = fuzzy_threshold
        self.review_threshold = review_threshold

        self._canonical_by_norm: dict[str, str] = {}
        self._alias_by_norm: dict[str, str] = {}
        self._all_norms_to_display: dict[str, str] = {}
        self._learned_canonicals: dict[str, str] = {}  # norm -> display name of a first-seen "new" entity

        for canonical, aliases in self.seed.items():
            norm_canonical = normalize_name(canonical)
            self._canonical_by_norm[norm_canonical] = canonical
            self._all_norms_to_display[norm_canonical] = canonical
            for alias in aliases:
                norm_alias = normalize_name(alias)
                self._alias_by_norm[norm_alias] = canonical
                self._all_norms_to_display.setdefault(norm_alias, canonical)

        self._fuzzy_corpus: list[str] = list(self._all_norms_to_display.keys())

    def resolve(self, raw_name: str) -> ResolutionResult:
        norm = normalize_name(raw_name)
        if not norm:
            return ResolutionResult(canonical_name=raw_name.strip(), method="unmatched-new", confidence=0.0)

        if norm in self._canonical_by_norm:
            return ResolutionResult(canonical_name=self._canonical_by_norm[norm], method="exact", confidence=100.0)

        if norm in self._alias_by_norm:
            return ResolutionResult(canonical_name=self._alias_by_norm[norm], method="alias", confidence=100.0)

        if norm in self._learned_canonicals:
            return ResolutionResult(canonical_name=self._learned_canonicals[norm], method="exact", confidence=100.0)

        if self._fuzzy_corpus:
            match = process.extractOne(norm, self._fuzzy_corpus, scorer=fuzz.token_sort_ratio)
            if match is not None:
                matched_norm, score, _ = match
                if score >= self.fuzzy_threshold:
                    return ResolutionResult(
                        canonical_name=self._all_norms_to_display[matched_norm], method="fuzzy", confidence=float(score)
                    )
                if score >= self.review_threshold:
                    # Below the auto-merge bar but close enough to be worth a
                    # human glance — logged, never silently auto-merged. This
                    # is exactly the band real false merges came from (90-96%)
                    # before fuzzy_threshold was raised; surfacing it instead
                    # of discarding it is what review_threshold is for.
                    logger.info(
                        "entity_resolution_near_match_not_merged",
                        raw_name=raw_name,
                        candidate=self._all_norms_to_display[matched_norm],
                        confidence=round(float(score), 1),
                    )

        display = raw_name.strip()
        self._learned_canonicals[norm] = display
        self._all_norms_to_display[norm] = display
        self._fuzzy_corpus.append(norm)
        return ResolutionResult(canonical_name=display, method="unmatched-new", confidence=0.0)


@dataclass
class MappingLogEntry:
    raw_name: str
    canonical_name: str
    method: str
    confidence: float
    source_url: Optional[str]
    timestamp: datetime  # kept as a real datetime — the DB insert needs one;
    # CSV writing formats it to ISO-8601 at the point of writing (see
    # MappingLogWriter.write), not here, so this one value stays valid for
    # both destinations instead of drifting into a string too early.

    @classmethod
    def build(cls, raw_name: str, result: ResolutionResult, source_url: Optional[str]) -> "MappingLogEntry":
        return cls(
            raw_name=raw_name,
            canonical_name=result.canonical_name,
            method=result.method,
            confidence=result.confidence,
            source_url=source_url,
            timestamp=datetime.now(timezone.utc),
        )

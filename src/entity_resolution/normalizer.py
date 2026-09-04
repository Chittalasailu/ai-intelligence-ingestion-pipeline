"""Deterministic text normalization for entity names — the first, cheap pass
before any fuzzy matching. Two names that normalize to the same string are
almost certainly the same entity; this alone resolves the "OpenAI, Inc." /
"OpenAI Inc" / "openai inc" family of variants without touching fuzzy logic.
"""
from __future__ import annotations

import re
import unicodedata

_LEGAL_SUFFIXES = [
    "incorporated", "inc", "llc", "l.l.c", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "srl", "s.r.l", "plc", "pte", "pte ltd", "pty", "pty ltd",
    "sa", "s.a", "ag", "bv", "b.v", "nv", "n.v", "kk", "k.k", "lp", "l.p", "pbc", "a.s",
]
# Deliberately excludes brand-ish words like "labs"/"technologies"/"group" —
# those are part of a company's identity, not a legal-entity designator, and
# auto-stripping them raises false-merge risk (e.g. an unrelated "X Labs").
# Those variants are instead handled explicitly via the seed alias table or
# fuzzy matching, which is auditable per-case rather than a blanket strip.
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s).replace(r"\ ", r"\s*") for s in _LEGAL_SUFFIXES) + r")\.?\s*$"
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_name(raw: str, strip_legal_suffix: bool = True) -> str:
    """Normalize to a comparison key: NFKD + strip accents -> lowercase ->
    remove punctuation -> collapse whitespace -> (optionally) strip a
    trailing legal-entity suffix, applied iteratively since names sometimes
    stack them ("... Inc., LLC").
    """
    text = strip_accents(raw)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()

    if strip_legal_suffix:
        prev = None
        while prev != text:
            prev = text
            text = _SUFFIX_RE.sub("", text).strip()
    return text

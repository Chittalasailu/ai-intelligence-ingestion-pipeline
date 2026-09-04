"""Deterministic role-family classifier: keyword rules over title/tags text.

This is the fallback path used when no LLM provider is configured (or the
LLM call fails); src/pipelines/jobs.py tries the LLM orchestrator first for
a richer classification and falls back to this so the pipeline never stalls
on a missing API key.
"""
from __future__ import annotations

_RULES: list[tuple[str, list[str]]] = [
    ("Engineering", ["engineer", "developer", "swe", "software", "backend", "frontend", "full stack", "fullstack", "devops", "sre", "infrastructure", "platform"]),
    ("Data Science / ML", ["machine learning", "ml engineer", "data scientist", "data science", "research scientist", "ai engineer", "applied scientist", "mlops", "llm"]),
    ("Research", ["research", "scientist"]),
    ("Product", ["product manager", "product owner", "pm "]),
    ("Design", ["designer", "ux", "ui", "product design"]),
    ("Sales", ["sales", "account executive", "business development", "bdr", "sdr"]),
    ("Marketing", ["marketing", "growth", "content", "seo", "brand"]),
    ("Customer Support", ["support", "customer success", "customer experience"]),
    ("Operations", ["operations", "ops", "recruiter", "people ", "hr ", "finance", "legal", "people ops"]),
]


def classify_role_family(title: str, extra_text: str = "") -> str:
    haystack = f" {title.lower()} {extra_text.lower()} "
    for family, keywords in _RULES:
        if any(kw in haystack for kw in keywords):
            return family
    return "Other"

"""Product pricing-model classification from a company's own live website.

We do NOT guess pricingModel from the company name/category. We fetch the
homepage (and, if a pricing link is visibly present in the nav, that page
too), extract visible text, and apply a documented, deterministic keyword
heuristic. If the signals are ambiguous or absent, the product is SKIPPED
rather than assigned a default — an unclassifiable page must not silently
become "PAID".

This is the rule-based fallback path; when an LLM provider is configured
(see src/llm/orchestrator.py), pipelines/products.py prefers asking the LLM
to classify the same extracted text against the same 4-way enum first, and
only falls back to this heuristic when no provider is available or the LLM
call fails — see docs/ARCHITECTURE for the reasoning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from src.schemas.models import PricingModel
from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_FREE_TIER_PATTERNS = [r"\bfree plan\b", r"\bfree tier\b", r"\bstart(s)? free\b", r"\bfree forever\b", r"\b100% free\b", r"\balways free\b", r"\bfree to use\b"]
_PAID_PRICE_PATTERNS = [r"\$\d+(\.\d{2})?\s*/\s*(mo|month|user|seat)", r"\$\d+(\.\d{2})?\s*per\s*(month|user|seat)", r"\busd?\s?\d+\s*/\s*(mo|month)"]
_ENTERPRISE_PATTERNS = [r"\bcontact sales\b", r"\btalk to sales\b", r"\bbook a demo\b", r"\bcustom pricing\b", r"\benterprise plan\b", r"\bcontact us for pricing\b", r"\brequest a quote\b"]

_FREE_RE = re.compile("|".join(_FREE_TIER_PATTERNS), re.IGNORECASE)
_PAID_RE = re.compile("|".join(_PAID_PRICE_PATTERNS), re.IGNORECASE)
_ENTERPRISE_RE = re.compile("|".join(_ENTERPRISE_PATTERNS), re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(no|not|without|n't have|never)\s+\w*\s*$", re.IGNORECASE)
_NEGATION_LOOKBACK_CHARS = 20


def _has_unnegated_match(pattern: re.Pattern, text: str) -> bool:
    """True if `pattern` matches somewhere NOT immediately preceded by a
    negation word — "no free tier available" must not register as a free
    signal just because the words "free tier" appear in it.
    """
    for match in pattern.finditer(text):
        preceding = text[max(0, match.start() - _NEGATION_LOOKBACK_CHARS) : match.start()]
        if not _NEGATION_RE.search(preceding):
            return True
    return False


@dataclass
class PricingClassification:
    pricing_model: Optional[PricingModel]
    evidence: str
    fetched_url: str


def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator=" ")


def find_pricing_link(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").strip().lower()
        if "pricing" in href.lower() or text == "pricing":
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                return base_url.rstrip("/") + href
    return None


def classify_from_text(text: str) -> Optional[PricingModel]:
    text_lower = text.lower()
    has_free = _has_unnegated_match(_FREE_RE, text_lower)
    has_paid_price = bool(_PAID_RE.search(text_lower))
    has_enterprise = bool(_ENTERPRISE_RE.search(text_lower))

    if has_free and (has_paid_price or has_enterprise):
        return PricingModel.FREEMIUM
    if has_free:
        return PricingModel.FREE
    if has_paid_price:
        return PricingModel.PAID
    if has_enterprise:
        return PricingModel.ENTERPRISE
    return None


class ProductPricingExtractor:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def classify(self, website_url: str) -> Optional[PricingClassification]:
        status, home_html, _headers = await self.http_client.fetch(website_url, max_retries_override=1)
        if status != 200 or not isinstance(home_html, str):
            return None

        home_text = extract_visible_text(home_html)
        result = classify_from_text(home_text)
        fetched_url = website_url

        if result is None:
            pricing_url = find_pricing_link(home_html, website_url)
            if pricing_url and pricing_url != website_url:
                p_status, pricing_html, _ = await self.http_client.fetch(pricing_url, max_retries_override=1)
                if p_status == 200 and isinstance(pricing_html, str):
                    pricing_text = extract_visible_text(pricing_html)
                    result = classify_from_text(pricing_text)
                    fetched_url = pricing_url
                    home_text = pricing_text

        if result is None:
            return None
        return PricingClassification(pricing_model=result, evidence=home_text[:300], fetched_url=fetched_url)

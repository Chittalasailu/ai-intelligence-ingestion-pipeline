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


_GUESSED_PRICING_PATHS = ["/pricing", "/plans", "/price", "/pricing/", "#pricing"]


class ProductPricingExtractor:
    def __init__(self, http_client: AsyncHttpClient):
        self.http_client = http_client

    async def _fetch(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Returns (html, actual_url_that_succeeded) — actual_url can differ
        from `url` when the https->http fallback below kicks in, and
        callers must attribute evidence to whichever URL really produced
        it, not the one they started with.
        """
        status, html, _headers = await self.http_client.fetch(url, max_retries_override=1)
        if status == 200 and isinstance(html, str):
            return html, url
        # A real fraction of small startup sites are misconfigured HTTPS
        # (expired/self-signed certs, no TLS at all) but still serve plain
        # HTTP — retrying once there recovers real, legitimate content
        # instead of just recording the site as unreachable.
        if url.startswith("https://") and status in (599, 598):
            http_url = "http://" + url[len("https://") :]
            alt_status, alt_html, _ = await self.http_client.fetch(http_url, max_retries_override=1)
            if alt_status == 200 and isinstance(alt_html, str):
                return alt_html, http_url
        return None, None

    async def classify(self, website_url: str) -> Optional[PricingClassification]:
        home_html, fetched_url = await self._fetch(website_url)
        if home_html is None or fetched_url is None:
            return None

        home_text = extract_visible_text(home_html)
        result = classify_from_text(home_text)

        if result is None:
            pricing_url = find_pricing_link(home_html, fetched_url)
            candidate_urls = [pricing_url] if pricing_url else []
            # No pricing link found in the nav — try the handful of paths
            # almost every SaaS site actually uses, rather than giving up.
            if not candidate_urls:
                base = fetched_url.rstrip("/")
                candidate_urls = [base + path for path in _GUESSED_PRICING_PATHS if path != "#pricing"]

            for candidate in candidate_urls:
                if candidate == fetched_url:
                    continue
                pricing_html, candidate_actual_url = await self._fetch(candidate)
                if pricing_html is None or candidate_actual_url is None:
                    continue
                pricing_text = extract_visible_text(pricing_html)
                candidate_result = classify_from_text(pricing_text)
                if candidate_result is not None:
                    result = candidate_result
                    fetched_url = candidate_actual_url
                    home_text = pricing_text
                    break

        if result is None:
            return None
        return PricingClassification(pricing_model=result, evidence=home_text[:300], fetched_url=fetched_url)

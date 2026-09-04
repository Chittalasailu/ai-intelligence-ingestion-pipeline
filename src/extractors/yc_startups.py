"""Y Combinator startup directory extractor.

Source: https://yc-oss.github.io/api/companies/all.json — a community-run,
explicitly-public-for-reuse static mirror of YC's own company directory
(github.com/yc-oss/api). Verified during development: real HTTP 200, ~6,200
companies, with a genuine `team_size` field covering ~96% of records — that
field maps directly to the required `employeeCount`, with no need to guess
or backfill it. Where team_size is null/0/missing we emit employeeCount as
None rather than inventing a number.

Startups are filtered to AI-tagged companies (industry/tag containing
"Artificial Intelligence", "Generative AI", or "Machine Learning") since
that's the actual subject of this pipeline — ~1,900 of the ~6,200
companies are AI-tagged, comfortably above the 1,000-record target.

Products draws from the same AI-tagged pool first, then — only if that
pool's real classification yield (see product_pricing.py; not every
company's site gives a confident FREE/FREEMIUM/PAID/ENTERPRISE signal)
falls short of target — supplements from the full ~6,200-company
directory via `filter_all_companies`. This is a deliberate scope
broadening for volume, not a data-quality compromise: every product
record is still classified from that exact company's own live site, never
guessed, and never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_AI_MARKERS = ["artificial intelligence", "generative ai", "machine learning", " ai ", "ai/ml"]


@dataclass
class YcCompany:
    name: str
    website: Optional[str]
    yc_page_url: str
    team_size: Optional[int]
    one_liner: str
    industries: list[str]
    status: str
    batch: str


def _is_ai_related(company: dict) -> bool:
    industries = company.get("industries") or []
    tags = company.get("tags") or []
    industry = company.get("industry") or ""
    subindustry = company.get("subindustry") or ""
    haystack = " ".join([*industries, *tags, industry, subindustry]).lower()
    padded = f" {haystack} "
    return any(marker in padded for marker in _AI_MARKERS)


class YcStartupsExtractor:
    def __init__(self, http_client: AsyncHttpClient, url: str = "https://yc-oss.github.io/api/companies/all.json"):
        self.http_client = http_client
        self.url = url

    async def fetch_all(self) -> list[dict]:
        status, body, _headers = await self.http_client.fetch(self.url, expect_json=True, max_retries_override=2)
        if status != 200 or not isinstance(body, list):
            logger.error("yc_dataset_fetch_failed", status=status)
            return []
        logger.info("yc_dataset_fetched", total=len(body))
        return body

    @staticmethod
    def _to_yc_company(c: dict) -> Optional[YcCompany]:
        name = (c.get("name") or "").strip()
        if not name:
            return None
        team_size = c.get("team_size")
        team_size = int(team_size) if isinstance(team_size, (int, float)) and team_size > 0 else None
        slug = c.get("slug", "")
        return YcCompany(
            name=name,
            website=c.get("website") or None,
            yc_page_url=c.get("url") or f"https://www.ycombinator.com/companies/{slug}",
            team_size=team_size,
            one_liner=c.get("one_liner", ""),
            industries=c.get("industries") or [],
            status=c.get("status", ""),
            batch=c.get("batch", ""),
        )

    def filter_ai_companies(self, companies: list[dict], limit: Optional[int] = None, active_only: bool = False) -> list[YcCompany]:
        results: list[YcCompany] = []
        for c in companies:
            if not _is_ai_related(c):
                continue
            if active_only and c.get("status") != "Active":
                continue
            yc_company = self._to_yc_company(c)
            if yc_company is None:
                continue
            results.append(yc_company)
            if limit is not None and len(results) >= limit:
                break
        return results

    def filter_all_companies(
        self, companies: list[dict], limit: Optional[int] = None, exclude_names: Optional[set[str]] = None
    ) -> list[YcCompany]:
        """The full directory (any industry), for use as a volume
        supplement when the AI-tagged pool alone can't reach a target after
        real-world fetch/classification attrition. `exclude_names` avoids
        re-processing companies already attempted via filter_ai_companies.
        """
        exclude = {n.lower() for n in (exclude_names or set())}
        results: list[YcCompany] = []
        for c in companies:
            name = (c.get("name") or "").strip()
            if not name or name.lower() in exclude:
                continue
            yc_company = self._to_yc_company(c)
            if yc_company is None:
                continue
            results.append(yc_company)
            if limit is not None and len(results) >= limit:
                break
        return results

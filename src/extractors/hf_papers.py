"""Hugging Face Daily Papers API: a curated, real, no-auth feed of trending
AI papers, cross-referenced with arXiv IDs. Used as a secondary/enrichment
source alongside the primary arXiv category crawl (see arxiv.py) — NOT a
replacement for Papers-with-Code, whose public API has been retired
(paperswithcode.com now redirects into huggingface.co/papers, confirmed by
inspecting the actual response during development — see docs/LIMITATIONS.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.utils.freshness import parse_datetime
from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_GITHUB_URL_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_\-\.]+/[A-Za-z0-9_\-\.]+")


@dataclass
class HfPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    paper_url: str
    published_date: datetime
    summary: str
    github_url_candidate: Optional[str]
    github_evidence_text: Optional[str]


class HfDailyPapersExtractor:
    def __init__(self, http_client: AsyncHttpClient, url: str = "https://huggingface.co/api/daily_papers"):
        self.http_client = http_client
        self.url = url

    async def fetch(self, limit: int = 100) -> list[HfPaper]:
        status, body, _headers = await self.http_client.fetch(f"{self.url}?limit={limit}", expect_json=True)
        if status != 200 or not isinstance(body, list):
            logger.warning("hf_daily_papers_fetch_failed", status=status)
            return []

        results: list[HfPaper] = []
        for item in body:
            try:
                paper = item.get("paper", {})
                arxiv_id = paper.get("id", "")
                if not arxiv_id:
                    continue
                title = item.get("title") or paper.get("title") or ""
                authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
                summary = paper.get("summary", "")
                published_raw = paper.get("publishedAt")
                published_date = parse_datetime(published_raw)
                if published_date is None:
                    continue

                github_url = None
                evidence = None
                match = _GITHUB_URL_RE.search(summary)
                if match:
                    github_url = match.group(0).rstrip(").,;")
                    evidence = summary

                results.append(
                    HfPaper(
                        arxiv_id=arxiv_id,
                        title=" ".join(title.split()),
                        authors=authors,
                        paper_url=f"https://arxiv.org/abs/{arxiv_id}",
                        published_date=published_date,
                        summary=summary,
                        github_url_candidate=github_url,
                        github_evidence_text=evidence,
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("hf_paper_parse_failed", error=str(e))
                continue
        return results

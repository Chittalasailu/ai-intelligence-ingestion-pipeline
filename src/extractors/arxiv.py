"""arXiv API extractor: the primary, highest-volume source of real AI
research papers (cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, stat.ML — each category
alone holds far more than 1,000 papers, so hitting the target is a matter of
pagination, not source scarcity).

GitHub association is extracted ONLY when the paper's own abstract or
author-comment field contains a literal github.com URL — i.e. the authors
themselves declared it. We never guess a repo from title similarity. This
is what src/schemas/validation.is_valid_github_evidence enforces downstream.

Politeness: arXiv's API terms ask for no more than ~1 request per 3 seconds
and no concurrent request bursts against /api/query, so this extractor is
deliberately sequential with a fixed delay, unlike the rest of the crawler
which fans out concurrently.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import feedparser

from src.utils.freshness import parse_datetime
from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_GITHUB_URL_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_\-\.]+/[A-Za-z0-9_\-\.]+")


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    paper_url: str
    pdf_url: Optional[str]
    published_date: datetime
    summary: str
    github_url_candidate: Optional[str]
    github_evidence_text: Optional[str]
    categories: list[str]


def _extract_github_evidence(*texts: str) -> tuple[Optional[str], Optional[str]]:
    for text in texts:
        if not text:
            continue
        match = _GITHUB_URL_RE.search(text)
        if match:
            url = match.group(0).rstrip(").,;")
            return url, text
    return None, None


def _clean_arxiv_id(raw_id: str) -> str:
    # raw_id looks like "http://arxiv.org/abs/2508.12345v1" -> "2508.12345"
    tail = raw_id.rstrip("/").split("/")[-1]
    return re.sub(r"v\d+$", "", tail)


class ArxivExtractor:
    def __init__(self, http_client: AsyncHttpClient, base_url: str = "https://export.arxiv.org/api/query", polite_delay_seconds: float = 3.0):
        self.http_client = http_client
        self.base_url = base_url
        self.polite_delay_seconds = polite_delay_seconds

    async def fetch_category(self, category: str, start: int, max_results: int) -> list[ArxivPaper]:
        query = f"search_query={quote(f'cat:{category}')}&start={start}&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        url = f"{self.base_url}?{query}"
        status, body, _headers = await self.http_client.fetch(url, expect_json=False)
        if status != 200 or not body:
            logger.warning("arxiv_fetch_failed", category=category, start=start, status=status)
            return []
        return self._parse_feed(body)

    def _parse_feed(self, xml_text: str) -> list[ArxivPaper]:
        parsed = feedparser.parse(xml_text)
        papers: list[ArxivPaper] = []
        for entry in parsed.entries:
            try:
                arxiv_id = _clean_arxiv_id(entry.get("id", ""))
                title = " ".join(entry.get("title", "").split())
                authors = [a.get("name", "").strip() for a in entry.get("authors", []) if a.get("name")]
                summary = " ".join(entry.get("summary", "").split())
                comment = entry.get("arxiv_comment", "") or ""
                published_raw = entry.get("published") or entry.get("updated")
                published_date = parse_datetime(published_raw)
                if published_date is None:
                    continue

                pdf_url = None
                paper_url = entry.get("link", f"https://arxiv.org/abs/{arxiv_id}")
                for link in entry.get("links", []):
                    if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                        pdf_url = link.get("href")

                github_url, evidence = _extract_github_evidence(summary, comment)
                categories = [t.get("term") for t in entry.get("tags", []) if t.get("term")]

                papers.append(
                    ArxivPaper(
                        arxiv_id=arxiv_id,
                        title=title,
                        authors=authors,
                        paper_url=paper_url,
                        pdf_url=pdf_url,
                        published_date=published_date,
                        summary=summary,
                        github_url_candidate=github_url,
                        github_evidence_text=evidence,
                        categories=categories,
                    )
                )
            except Exception as e:  # noqa: BLE001 - one malformed entry must not drop the whole page
                logger.warning("arxiv_entry_parse_failed", error=str(e))
                continue
        return papers

    async def iter_category(self, category: str, page_size: int, max_papers: int):
        """Sequential, politely-delayed pagination generator — yields one
        ArxivPaper at a time up to max_papers for this category.
        """
        fetched = 0
        start = 0
        while fetched < max_papers:
            batch = await self.fetch_category(category, start, min(page_size, max_papers - fetched))
            if not batch:
                return
            for paper in batch:
                yield paper
                fetched += 1
                if fetched >= max_papers:
                    return
            start += len(batch)
            await asyncio.sleep(self.polite_delay_seconds)

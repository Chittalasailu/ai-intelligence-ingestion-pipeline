"""GitHub star-count enrichment, with persistent caching and honest
rate-limit handling.

Unauthenticated GitHub API access is capped at 60 requests/hour per IP —
nowhere near enough for 1,000+ papers. With GITHUB_TOKEN set it's 5,000/hour,
which is enough for a real demo batch. Either way we: cache every lookup to
disk so a repeat run costs zero extra requests, track remaining quota from
response headers, and stop issuing new requests (rather than 403-looping)
once the quota is exhausted — returning `None` for stars on the remaining
papers instead of fabricating a number.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

from src.utils.http_client import AsyncHttpClient
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_REPO_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_\-\.]+)/([A-Za-z0-9_\-\.]+)")


class GithubStarsCache:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, Optional[int]] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def has(self, repo: str) -> bool:
        return repo in self._data

    def get_cached(self, repo: str) -> Optional[int]:
        return self._data.get(repo)

    def set(self, repo: str, stars: Optional[int]) -> None:
        with self._lock:
            self._data[repo] = stars
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


class GithubStarsClient:
    def __init__(self, http_client: AsyncHttpClient, token: str, cache: GithubStarsCache):
        self.http_client = http_client
        self.token = token
        self.cache = cache
        self._quota_remaining: Optional[int] = None
        self._quota_exhausted = False

    @staticmethod
    def parse_repo_slug(github_url: str) -> Optional[str]:
        match = _REPO_URL_RE.search(github_url)
        if not match:
            return None
        owner, repo = match.group(1), match.group(2)
        repo = repo.removesuffix(".git")
        return f"{owner}/{repo}"

    async def get_stars(self, github_url: str) -> Optional[int]:
        slug = self.parse_repo_slug(github_url)
        if not slug:
            return None
        if self.cache.has(slug):
            return self.cache.get_cached(slug)
        if self._quota_exhausted:
            logger.info("github_quota_exhausted_skipping", repo=slug)
            return None

        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        status, body, resp_headers = await self.http_client.fetch(
            f"https://api.github.com/repos/{slug}", expect_json=True, headers=headers, max_retries_override=1
        )
        remaining = resp_headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            self._quota_remaining = int(remaining)
            if self._quota_remaining <= 0:
                self._quota_exhausted = True

        if status == 200 and isinstance(body, dict):
            stars = body.get("stargazers_count")
            self.cache.set(slug, stars)
            return stars
        if status == 404:
            logger.info("github_repo_not_found", repo=slug)
            self.cache.set(slug, None)
            return None
        if status == 403:
            self._quota_exhausted = True
            logger.warning("github_rate_limited_or_forbidden", repo=slug)
            return None

        logger.warning("github_stars_lookup_failed", repo=slug, status=status)
        return None

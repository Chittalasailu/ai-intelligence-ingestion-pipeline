"""End-to-end pipeline flow tests with all network calls mocked
(aioresponses) and a temp-file SQLite database — no real internet access,
no real API keys, no real GitHub calls. This proves the full chain (fetch
-> parse -> validate -> entity-resolve -> store -> export-row) wires
together correctly, independent of whether live sources are reachable
when this suite runs.
"""
from __future__ import annotations

import re
from pathlib import Path

from aioresponses import aioresponses

_ARXIV_URL_PATTERN = re.compile(r"^https://export\.arxiv\.org/api/query\?.*$")

from src.llm.factory import build_orchestrator
from src.pipelines.context import build_run_context
from src.pipelines.research_papers import run_research_papers_pipeline
from src.storage.db import create_engine, init_db, make_session_factory
from src.utils.config import Settings

_ARXIV_ATOM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2508.00001v1</id>
    <title>A Paper With A Real GitHub Repo</title>
    <summary>We propose a method. Code available at https://github.com/example-org/real-repo for reproducibility.</summary>
    <published>2026-08-01T00:00:00Z</published>
    <updated>2026-08-01T00:00:00Z</updated>
    <author><name>Jane Doe</name></author>
    <author><name>John Smith</name></author>
    <link href="http://arxiv.org/abs/2508.00001v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2508.00001v1" rel="related" type="application/pdf"/>
    <category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2508.00002v1</id>
    <title>A Paper With No Repo Mentioned</title>
    <summary>We study a different method with no code release.</summary>
    <published>2026-08-02T00:00:00Z</published>
    <updated>2026-08-02T00:00:00Z</updated>
    <author><name>Ada Lovelace</name></author>
    <link href="http://arxiv.org/abs/2508.00002v1" rel="alternate" type="text/html"/>
    <category term="cs.AI"/>
  </entry>
</feed>"""


def _test_settings(tmp_path: Path) -> Settings:
    return Settings(
        raw_settings={
            "crawler": {"max_concurrency": 5, "per_host_concurrency": 2, "request_timeout_seconds": 5, "max_retries": 1},
            "llm": {"provider_order": []},
            "entity_resolution": {"fuzzy_match_threshold": 90, "review_threshold": 80},
        },
        raw_sources={
            "research_papers": [
                {
                    "name": "arXiv API",
                    "adapter": "arxiv",
                    "base_url": "https://export.arxiv.org/api/query",
                    "categories": ["cs.AI"],
                    "page_size": 100,
                    "polite_delay_seconds": 0.001,
                },
            ]
        },
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_concurrency=5,
        per_host_concurrency=2,
        request_timeout_seconds=5,
        max_retries=1,
    )


async def test_research_papers_pipeline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "data" / "mappings").mkdir(parents=True)

    settings = _test_settings(tmp_path)
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    async with build_orchestrator(settings) as llm:
        ctx = build_run_context(settings, session_factory, llm, run_id="test-run", data_root=tmp_path)
        async with ctx.http_client:
            with aioresponses() as m:
                m.get(_ARXIV_URL_PATTERN, body=_ARXIV_ATOM_TEMPLATE, status=200, repeat=True)
                m.get(
                    "https://api.github.com/repos/example-org/real-repo",
                    payload={"stargazers_count": 777},
                    status=200,
                    headers={"X-RateLimit-Remaining": "59"},
                    repeat=True,
                )
                rows = await run_research_papers_pipeline(ctx, target=10)

    await engine.dispose()

    assert len(rows) == 2
    with_repo = next(r for r in rows if "real-repo" in (r["github_url"] or ""))
    without_repo = next(r for r in rows if r["title"] == "A Paper With No Repo Mentioned")

    assert with_repo["github_url"] == "https://github.com/example-org/real-repo"
    assert with_repo["github_stars"] == 777
    assert without_repo["github_url"] == ""  # never fabricated when no evidence exists
    assert without_repo["github_stars"] == ""
    assert ctx.stats.records_validated == 2
    assert ctx.stats.records_rejected == 0


async def test_research_papers_pipeline_is_resumable_across_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "data" / "mappings").mkdir(parents=True)

    settings = _test_settings(tmp_path)
    engine = create_engine(settings.database_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    async def _run_once():
        async with build_orchestrator(settings) as llm:
            ctx = build_run_context(settings, session_factory, llm, run_id="test-run", data_root=tmp_path)
            async with ctx.http_client:
                with aioresponses() as m:
                    m.get(_ARXIV_URL_PATTERN, body=_ARXIV_ATOM_TEMPLATE, status=200, repeat=True)
                    m.get(
                        "https://api.github.com/repos/example-org/real-repo",
                        payload={"stargazers_count": 777},
                        status=200,
                        headers={"X-RateLimit-Remaining": "59"},
                        repeat=True,
                    )
                    return await run_research_papers_pipeline(ctx, target=10)

    first_rows = await _run_once()
    second_rows = await _run_once()

    await engine.dispose()

    assert len(first_rows) == 2
    assert len(second_rows) == 0  # checkpoint already saw both arxiv IDs — no duplicates re-emitted

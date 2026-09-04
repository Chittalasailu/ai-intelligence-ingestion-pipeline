# AI Intelligence Ingestion Pipeline

A production-shaped, async data pipeline that ingests AI startups, products,
research papers, news, and job postings from legitimate public sources,
normalizes and validates every record, canonicalizes entity names, and
exports the result to CSV/XLSX/Google Sheets.

Built for the GraphOne / FrontierAtlas AI Engineer take-home assignment.
Every record in `data/` was produced by actually running this code against
live sources — see [Data actually collected](#data-actually-collected) for
exact counts and [Limitations](#limitations) for what real-world sources
did and didn't allow.

## Overview

The pipeline covers all five verticals the assignment specifies:

| Vertical | Source(s) | Method |
|---|---|---|
| Startups | Y Combinator public directory (via the open [yc-oss](https://github.com/yc-oss/api) mirror) | Bulk JSON fetch, AI-tag filter, entity resolution |
| Products | Same YC companies' live websites | Concurrent fetch + pricing-model classification (LLM-first, keyword-heuristic fallback) |
| Research papers | arXiv API + Hugging Face Daily Papers API | Paginated fetch, author-declared GitHub link extraction, GitHub star lookup |
| News | 5 AI-news RSS feeds | Feed parse, full-text fetch, strict 24h freshness gate |
| Jobs | RemoteOK, WeWorkRemotely, 3× Greenhouse boards | API/RSS fetch, role classification, strict 24h freshness gate |

No step here bypasses a paywall, CAPTCHA, or bot-detection system. Every
source is either an official API, an RSS/Atom feed meant for syndication,
or a statically-published open dataset. See
[Anti-bot strategy](#anti-bot-strategy) for the reasoning and what a
Cloudflare-protected source would get instead.

## Architecture

```
                    ┌─────────────────┐
                    │  config/*.yaml   │  sources, concurrency, thresholds
                    └────────┬─────────┘
                             │
   ┌─────────────────────────▼──────────────────────────┐
   │                    src/crawler/                      │
   │  run_bounded()  — semaphore-bounded concurrency,      │
   │  per-task failure isolation, graceful shutdown        │
   └─────────────────────────┬──────────────────────────┘
                             │  (calls into)
   ┌─────────────────────────▼──────────────────────────┐
   │                  src/extractors/                     │
   │  arxiv · hf_papers · github_stars · yc_startups ·     │
   │  product_pricing · news_rss · jobs                    │
   │  (each wraps src/utils/http_client — pooled aiohttp,  │
   │   retry+backoff+jitter, 429/timeout handling)         │
   └───────┬───────────────────────────────┬─────────────┘
           │ raw structured data            │ raw HTML/text
           │                                ▼
           │                    ┌───────────────────────┐
           │                    │      src/llm/           │
           │                    │  chunk → provider chain  │
           │                    │  Gemini → Groq → DeepSeek │
           │                    │  (413/429 handling,       │
           │                    │   structured JSON out)    │
           │                    └───────────┬───────────┘
           ▼                                ▼
   ┌──────────────────────────────────────────────────┐
   │              src/schemas/validation.py              │
   │   pydantic schema check → freshness check →         │
   │   reject-and-log if either fails                    │
   └───────────────────────┬──────────────────────────┘
                           ▼
   ┌──────────────────────────────────────────────────┐
   │           src/entity_resolution/                    │
   │  normalize → seed/alias exact match → fuzzy match    │
   │  → mapping log (CSV + DB)                            │
   └───────────────────────┬──────────────────────────┘
                           ▼
   ┌──────────────────────────────────────────────────┐
   │                 src/storage/                        │
   │   SQLAlchemy async ORM → SQLite (demo) or            │
   │   Postgres (DATABASE_URL swap, no code change)       │
   └───────────────────────┬──────────────────────────┘
                           ▼
   ┌──────────────────────────────────────────────────┐
   │                 src/export/                         │
   │   CSV per tab · one XLSX workbook · Google Sheets    │
   │   (all three read from the DB, not from one run's    │
   │    in-memory result — see Freshness/Resumability)    │
   └──────────────────────────────────────────────────┘
```

Every pipeline (`src/pipelines/*.py`) wires these layers together for one
vertical; `src/main.py` is the CLI that runs one or all of them and prints a
quality-stats summary; `scripts/export_data.py` re-exports the current
database state without touching a crawler.

## Features

- **Async everywhere**: `aiohttp` + `asyncio`, connection pooling, global
  and per-host concurrency limits (`src/utils/http_client.py`).
- **Real retry/backoff**: exponential backoff with jitter, `Retry-After`
  header support, non-retryable errors (404/400/403) fail fast instead of
  being retried (`src/utils/retry.py`).
- **Checkpointed & resumable**: every crawl records what it's already
  processed in a SQLite checkpoint store; a killed run resumes without
  re-fetching or re-emitting duplicates (`src/utils/checkpoint.py`).
- **Failure isolation**: `run_bounded()` catches per-item exceptions so one
  bad record never aborts a batch (`src/crawler/base.py`).
- **Multi-tier LLM orchestration**: Gemini → Groq → DeepSeek fallback chain
  with intelligent chunking, 413 shrink-and-retry, 429 backoff-and-fallback
  (`src/llm/orchestrator.py`).
- **Deterministic entity resolution**: Unicode/casing/punctuation/legal-
  suffix normalization, a 55-entity seed+alias table, fuzzy matching with a
  confidence threshold, and a full raw→canonical audit log
  (`src/entity_resolution/`).
- **Strict freshness gate**: news/jobs are rejected (not assumed fresh) if
  their publish date can't be determined; relative dates ("2 hours ago"),
  RFC-822, ISO-8601, and epoch timestamps are all normalized
  (`src/utils/freshness.py`).
- **No fabrication**: GitHub repos are only attached to a paper when the
  paper's own abstract/comment contains that exact URL
  (`src/schemas/validation.is_valid_github_evidence`); pricing models are
  only assigned when the company's own site gives a real signal; missing
  values are `null`, never guessed.
- **118 automated tests**, unit + integration, all passing (see
  [Testing](#testing)).

## Setup

```bash
git clone <this-repo-url>
cd ai-intelligence-ingestion-pipeline
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` works as-is for a demo run — every field has a safe default (SQLite,
no LLM keys). Fill in real values to unlock LLM-assisted extraction, higher
GitHub API limits, and Google Sheets export (see
[Environment Variables](#environment-variables)).

## Environment Variables

All in `.env.example`. None are required to run the demo; each unlocks one
capability when set.

| Variable | Required for | Effect if unset |
|---|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | LLM tier 1 | Orchestrator skips straight to Groq |
| `GROQ_API_KEY`, `GROQ_MODEL` | LLM tier 2 | Skips to DeepSeek |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | LLM tier 3 | If all three are unset, extraction falls back to the deterministic heuristics (pricing keywords, role-family keyword rules) — the pipeline still runs end-to-end |
| `GITHUB_TOKEN` | Research-paper GitHub star lookups at scale | Falls back to 60 requests/hour (shared per IP) instead of 5,000/hour |
| `DATABASE_URL` | Storage backend | Defaults to a local SQLite file — see [Storage](#storage) |
| `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEET_ID` | Google Sheets export | Export falls back to CSV/XLSX only — see [docs/GOOGLE_SHEETS_SETUP.md](docs/GOOGLE_SHEETS_SETUP.md) |
| `MAX_CONCURRENCY`, `PER_HOST_CONCURRENCY`, `REQUEST_TIMEOUT_SECONDS`, `MAX_RETRIES` | Crawler tuning | Sensible defaults (25 / 5 / 20s / 4) |
| `FRESHNESS_WINDOW_HOURS` | News/jobs freshness | Defaults to 24 |

## Running

```bash
# Everything (startups, products, research papers, news, jobs)
python -m src.main --pipeline all

# One vertical at a time
python -m src.main --pipeline startups --startups-target 1000
python -m src.main --pipeline products --products-target 1000
python -m src.main --pipeline research_papers --papers-target 1000
python -m src.main --pipeline news
python -m src.main --pipeline jobs

# A quick sample run (small targets, fast)
python -m src.main --pipeline research_papers --papers-target 20

# Re-export the current database to CSV/XLSX (and optionally Sheets)
# without re-running any crawler
python -m scripts.export_data
python -m scripts.export_data --sheets

# Tests
pytest -q
```

Every run prints a per-tab "new this run / total in database" summary plus
the full quality-stats counters (discovered/fetched/validated/rejected/
duplicates/429s/413s/freshness failures).

## Scaling to 500,000+ records

Nothing in the code path changes going from 1,000 to 500,000 records —
only configuration and infrastructure do:

1. **Concurrency is a config value, not a code path.** `MAX_CONCURRENCY`
   and `PER_HOST_CONCURRENCY` in `.env` are the only knobs `AsyncHttpClient`
   reads; raising them (and running more worker processes/containers, each
   with its own concurrency budget) is the entire scale-up story.
2. **Checkpointing makes horizontal scaling safe.** `CheckpointStore` is
   namespaced per source; splitting one source's ID space (e.g. arXiv
   categories, or YC batches) across N worker processes each with their own
   checkpoint file — or a shared Postgres-backed checkpoint table in
   production — is a partitioning problem, not a rewrite.
3. **Storage swaps from SQLite to Postgres via `DATABASE_URL` alone** — the
   SQLAlchemy models are unchanged; see [Storage](#storage).
4. **Pagination is already generator-based** (`ArxivExtractor.iter_category`,
   `RateLimitedPager` in `src/crawler/base.py`), so "fetch more" is a loop
   bound, not new logic.

Full reasoning, plus the distributed-freshness and dedup story, is in
[architecture.pdf](architecture.pdf).

## Error handling

**429 (rate limit)**: `src/utils/retry.py` backs off exponentially with
jitter, honoring a `Retry-After` header when the provider sends one
(`RateLimitError.retry_after`). Exhausting the configured retry budget on
one LLM provider falls through to the next tier in the chain
(`src/llm/orchestrator.py::_call_with_413_handling`) rather than continuing
to hammer a throttled provider.

**413 (payload too large)**: content is HTML-cleaned and chunked to a
token budget *before* the first request (`src/utils/chunking.py` — strips
boilerplate, finds the main content block, packs paragraphs into
budgeted chunks with overlap, always keeps the lead paragraph). If a
provider still 413s, `shrink_for_413` halves the budget and retries that
provider up to `max_413_retries` times before falling through to the next
provider.

**Timeouts / connection failures**: `AsyncHttpClient.fetch()` always
returns a `(status, body, headers)` tuple — it never raises. A dead host,
DNS failure, or timeout becomes a status code (598/599) the caller checks,
so one unreachable site can't crash a 1,000-item batch (this is enforced
by `src/crawler/base.run_bounded`'s per-task isolation as a second layer).

**Provider fallback**: `LLMOrchestrator.configured_providers` filters out
any tier with no API key, so the chain degrades gracefully — running with
zero keys still produces valid output via the deterministic fallbacks in
`product_pricing.py` and `role_family.py`.

## Freshness (news & jobs)

`src/utils/freshness.py` normalizes ISO-8601, RFC-822/RSS `pubDate`, epoch
seconds/millis, and relative strings ("2 hours ago", "yesterday") to UTC.
**A date that can't be parsed is never treated as fresh** — `is_fresh()`
returns `False` for `None`, and the one documented heuristic fallback
(`heuristic_is_new`, for a source with no timestamp at all) only fires
against real checkpoint state from a *previous* crawl, never on a source's
first-ever run — so a full backlog can't flood in as false-fresh on day
one. Every item that fails the check is written to the rejection log with
its parsed (or unparsed) date, not silently dropped — see the
`freshness_failures` counter in the run summary.

## Entity resolution

`src/entity_resolution/`:

1. **Normalize** (`normalizer.py`): Unicode NFKD + accent-strip → lowercase
   → strip punctuation → collapse whitespace → strip a trailing *legal*
   suffix (Inc, LLC, Corp, GmbH, ...) iteratively. Brand words like "Labs"
   or "Technologies" are deliberately **not** auto-stripped — stripping
   those would raise false-merge risk (an unrelated "X Labs" colliding with
   "X"); those variants are instead handled by the explicit alias table.
2. **Match**: exact normalized match against a 55-entity seed+alias table
   (`seed_data.py`) → alias table → fuzzy match (`rapidfuzz`
   `token_sort_ratio`, default threshold 90) → otherwise the input becomes
   a new canonical entity (so future duplicates of *it* still merge,
   without gluing it onto something unrelated).
3. **Log everything**: every resolution — including "no match, new
   canonical" — is written to `data/mappings/entity_mapping_log.csv` (and
   mirrored to the database) with its method and confidence, so the log is
   a complete audit trail, not just the interesting cases.

Example: `"OpenAI, Inc."`, `"OpenAI Inc"`, `"Open AI"`, `"openai inc"` all
resolve to `"OpenAI"` — see `tests/test_entity_resolution.py`.

## Anti-bot strategy

All configured sources here are ones that *want* to be consumed
programmatically: official REST APIs (arXiv, GitHub, Hugging Face,
Greenhouse, RemoteOK), RSS/Atom feeds (news, WeWorkRemotely), or a
statically-published open dataset (YC via yc-oss). None require solving a
CAPTCHA or defeating Cloudflare/Datadome, so none of that machinery was
built — doing so against a source's wishes wasn't in scope, per the
assignment's own instruction to prefer legitimate alternatives and
document the limitation instead.

For a genuinely JavaScript-rendered or bot-protected high-value source, the
documented approach (see [architecture.pdf](architecture.pdf)) is
Playwright with a persistent browser context, randomized per-domain rate
limiting, and `robots.txt`/ToS compliance checks before crawling — falling
back to an official API, RSS feed, or a different legitimate source when a
target is actively hostile to automation, rather than escalating to
CAPTCHA-solving.

## Data quality

Every record passes through `src/schemas/validation.validate_record`
before it can reach storage or export. A record is rejected (logged to
`logs/rejected_<run_id>.jsonl` with a reason, never silently dropped) when:

- its source URL fails pydantic's `HttpUrl` check,
- a required field is missing or the wrong type,
- `recordType` doesn't match the model,
- `pricingModel` isn't one of `FREE|FREEMIUM|PAID|ENTERPRISE`,
- a news/job item's publish date is missing, unparseable, or outside the
  24-hour freshness window,
- it duplicates an existing `dedup_key` (content-hash unique constraint —
  every table in `src/storage/models.py` has one).

The run summary printed by `src/main.py` reports
`records_discovered/fetched/parsed/validated/rejected`,
`duplicates_removed`, `llm_successes/failures`, `retries_429/413`, and
`freshness_failures` for every run — this is what actually ran, not an
estimate.

## Storage

**Primary database: PostgreSQL** (via `DATABASE_URL=postgresql+asyncpg://...`,
`docker-compose.yml` provisions one with the `pgvector` extension
pre-installed). Reasoning: the workload is relational at its core (typed
records with foreign-key-shaped relationships — a Product belongs to a
Startup, a Job belongs to a canonicalized Company), needs real ACID
transactions under concurrent writers (many crawler workers inserting at
once), and needs to scale past what SQLite's single-writer model can do in
production. `pgvector` gives an embedding column type in the *same*
database for the relationship/similarity work described below, instead of
standing up a second system just for that.

**The demo run in this repo uses the SQLite default** — zero setup,
identical `SQLAlchemy` models, so the schema is proven before you ever
touch Postgres. Switching is a one-line `.env` change.

**Relationship/vector strategy**: rather than standing up a graph database
for a trial, the extensible design is `pgvector` columns on the existing
Postgres tables — embed entity names/descriptions once (via any embedding
model) and use `pgvector`'s ANN index for "similar startups" or "similar
papers" queries. If relationship traversal becomes the dominant query
pattern at real scale (multi-hop "startups founded by alumni of X"), the
same Postgres data exports cleanly into Neo4j; standing that up
speculatively for a 1,000-record trial would be infrastructure for its own
sake.

## Output

`src/export/tabular.py` builds the exact 6 tabs the assignment specifies
(Startups, Products, Research Papers, Jobs, News, Entity Mapping Log) as
plain row-dicts, then:

- writes one CSV per tab under `data/<vertical>/`,
- writes one combined `.xlsx` workbook with frozen header rows and
  auto-sized columns,
- (optionally) pushes the same rows to Google Sheets via
  `src/export/google_sheets.py`.

All three always read from the **database**, not from one run's in-memory
result — see the freshness/resumability note in
[Scaling](#scaling-to-500000-records). Re-running an already-populated
pipeline correctly reports "0 new" without shrinking the export.

**Google Sheets**: requires a one-time GCP service-account setup — see
[docs/GOOGLE_SHEETS_SETUP.md](docs/GOOGLE_SHEETS_SETUP.md). That
credential step needs your Google account and isn't something this
pipeline can do for you; everything else (CSV/XLSX generation, the upload
call itself once credentials exist) is automatic via
`python -m scripts.export_data --sheets`.

## Data actually collected

Produced by running this exact code against live sources — see
[Limitations](#limitations) for why Products and Jobs land below their
"minimum 1,000" target and why that's the correct, honest outcome rather
than a bug to paper over.

| Tab | Count | Source |
|---|---:|---|
| Startups | 1,247 | YC directory, AI-tagged, real `team_size` where published |
| Products | 768 | Live company-website pricing classification (1,961 candidate sites fetched; see Limitations) |
| Research Papers | 1,000 | arXiv + Hugging Face Daily Papers, real GitHub links/stars where evidenced |
| News | 31 | 5 RSS feeds, strictly ≤24h old |
| Jobs | 11 | 5 job boards, strictly ≤24h old |
| Entity Mapping Log | 1,532 | Every resolution performed above |

(These are a snapshot from the run that produced the committed `data/*.csv` files. arXiv, RSS, and job-board content changes continuously — a fresh `python -m src.main --pipeline all` run will get different, still-real, numbers in the same range for News/Jobs and will only grow Startups/Products/Research Papers, never shrink them, since export always reflects the full database.)

## Testing

118 tests, all passing: `pytest -q`.

- **Unit**: date parsing & freshness (`test_freshness.py`), entity
  normalization/matching (`test_entity_resolution.py`), schema validation
  (`test_schemas.py`), chunking (`test_chunking.py`), retry/backoff
  (`test_retry.py`), dedup (`test_dedup.py`, `test_checkpoint.py`), pricing
  heuristic (`test_product_pricing.py`), mapping-log dedup
  (`test_mapping_log.py`).
- **Integration**: bounded-concurrency + failure isolation
  (`test_crawler_base.py`), LLM fallback chain against mocked HTTP
  (`test_llm_orchestrator.py`), GitHub star lookup + caching
  (`test_github_stars.py`), a real (temp-file) SQLite DB round-trip
  (`test_repository.py`), and a full pipeline run — fetch → validate →
  resolve → store — against a mocked arXiv response
  (`test_pipeline_integration.py`).

Several of these tests exist *because* running the pipeline against real
sources surfaced real bugs during development (a crash in the stats
reporter, a BeautifulSoup mutate-while-iterating crash, a SQLite
concurrency error, a checkpoint-ordering data-loss bug, a datetime/string
type mismatch) — each one is now a regression test, not just a fix.

## Limitations

Stated plainly, per the assignment's own anti-hallucination requirement —
nothing below is padded with synthetic records to hit a number.

- **Products landed at 768, short of the 1,000 target.** This pipeline
  exhausted the entire pool of AI-tagged YC companies with a website
  (1,923 total in the dataset; 1,961 fetch attempts made once retries and
  the pricing-page follow-up fetch are counted) — there wasn't a larger
  legitimate candidate pool available under the "AI startups" scope to draw
  from. Of those attempts, real-world DNS failures (shut-down startups),
  TLS errors, timeouts, and homepages with no confident pricing signal
  account for the gap. The pipeline never invents a pricing model for a
  site it couldn't classify — it skips that company entirely rather than
  guessing. Widening scope beyond AI-tagged companies (the full ~6,200-
  company YC directory) would close the numeric gap but was deliberately
  not done here, since it would dilute the "AI intelligence" focus the
  assignment is actually about.
- **Jobs and News are intentionally small.** The assignment asks for "all
  24-hour-fresh jobs/news found," not a minimum count — 749 of 760 job
  postings discovered were correctly rejected as older than 24 hours. A
  higher-frequency scheduled run (hourly, via cron) would compound to a
  much larger fresh set over a day without changing anything about the
  freshness logic itself.
- **GitHub star coverage is rate-limited without a token.** Unauthenticated
  GitHub API access is 60 requests/hour, shared per IP; with `GITHUB_TOKEN`
  set this becomes 5,000/hour. Papers with a declared repo but no
  fetched star count show `github_stars` as empty, never a guessed number.
- **No LLM provider keys were available in this environment**, so every
  run in this repo's `data/` used the deterministic fallback path
  (keyword-based pricing/role classification). The LLM orchestration code
  is real, tested against mocked provider responses
  (`test_llm_orchestrator.py`), and will activate automatically the moment
  `GEMINI_API_KEY`/`GROQ_API_KEY`/`DEEPSEEK_API_KEY` are set — this was not
  possible to verify against a live provider from this environment.
- **Papers with Code's public API is defunct** as of this writing —
  `paperswithcode.com/api/...` now redirects into `huggingface.co/papers`
  (confirmed by inspecting the actual response during development, not
  assumed). The research-papers pipeline uses arXiv + Hugging Face Daily
  Papers instead, extracting GitHub links only when the paper's own
  abstract/comment states one — see [Anti-hallucination](#no-fake-data).
- **Google Sheets was not pushed automatically** — it requires a
  GCP service-account credential this environment doesn't have and
  shouldn't generate on your behalf. See
  [docs/GOOGLE_SHEETS_SETUP.md](docs/GOOGLE_SHEETS_SETUP.md) for the exact
  one-time step; `python -m scripts.export_data --sheets` does the rest
  once that file exists.
- **This repository was not pushed to GitHub automatically** — no `gh` CLI
  and no stored GitHub credentials were available in this environment.
  See the final report for the exact command to run.

## No fake data

Every record traces to a real, checkable URL. GitHub repositories are only
attached to a paper when the paper's own text contains that exact URL
(never inferred from title similarity). Missing values (`employeeCount`,
`github_stars`, `github_url`) are `null`, never a placeholder. Where a
legitimate source couldn't produce enough volume, the shortfall is
reported above, not filled with synthetic rows.

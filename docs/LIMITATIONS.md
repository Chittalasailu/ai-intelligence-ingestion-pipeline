# Known limitations

Consolidated detail behind the README's Limitations section — the
technical "why," for anyone extending this pipeline.

## Fuzzy entity matching: measured, found unreliable, made conservative

Entity resolution's fuzzy-match tier (`rapidfuzz.fuzz.token_sort_ratio`)
was originally set to a 90% auto-merge threshold. Auditing every fuzzy
match this resolver ever produced against the real, committed dataset
turned up 8 matches — and all 8 were false merges of genuinely different
real companies, confirmed against their actual YC one-liners and websites:

| Raw name | Wrongly merged into | Score | Actually |
|---|---|---:|---|
| Shaped | Shape | 90.9% | real-time retrieval engine (shaped.ai) vs. a BI/analytics tool (shape.xyz) |
| Sharpe | Shape | 90.9% | quant-research agents (sharpe.sh) vs. the same BI tool |
| Serra | Sierra (seeded) | 90.9% | AI recruiter (serra.io) vs. AI customer-service platform |
| Aluna | Alguna | 90.9% | biomedical AI data (alunadata.com) vs. CPQ/billing software |
| Besimple AI | Simple AI | 90.0% | voice-data-for-AI (besimple.ai) vs. AI sales voice agent (usesimple.ai) |
| Lever | Clever | 90.9% | talent-acquisition ATS (lever.co) vs. classroom-tech platform (clever.com) |
| Tella | Trella | 90.9% | screen recorder (tella.com) vs. freight/logistics platform (trella.app) |
| Cair Health | Caire Health | 95.65% | AI healthcare-RCM agents vs. semi-autonomous diagnostics — different companies, different products |

Zero of the 8 were a legitimate same-company typo catch. The pattern is
structural, not a tuning fluke: a short, common-word company name plus one
inserted or changed character routinely still scores 90-96% on generic
string similarity, because the metric has no notion that "Lever" and
"Clever" are unrelated businesses — it only sees five correct characters
out of six.

**Fix**: `fuzzy_threshold` default raised to 97 (`src/entity_resolution/resolver.py`),
chosen to sit above the highest false positive actually observed (95.65%)
with margin. All 8 cases are now permanent regression tests
(`tests/test_entity_resolution.py`). `review_threshold` (80), previously
defined but unused, now does real work: a score between 80 and 97 is
logged (`entity_resolution_near_match_not_merged`) for visibility instead
of being silently discarded — the assignment's own stated cost asymmetry
(an incorrect merge corrupts data; a missed merge just leaves two records
slightly less consolidated) is why this defaults to precision over recall.
The 55-entity seed+alias table — not fuzzy matching — is what actually
carries the "OpenAI, Inc." / "Open AI" canonicalization requirement.

All 8 already-shipped false merges were corrected in the committed
database and re-exported (a targeted fix — not a full pipeline re-run —
since the affected records were individually identifiable by source URL).
`data/mappings/entity_mapping_log.csv` and `data/startups/startups.csv` /
`data/products/products.csv` in this repository reflect the corrected
state; zero duplicate canonical names remain across either output file as
of the last export.

## Papers with Code is defunct

`config/sources.yaml` doesn't use `paperswithcode.com`'s API. During
development, `curl https://paperswithcode.com/api/v1/papers/` returned
Hugging Face's own "Trending Papers" HTML page, not JSON — the domain now
redirects into `huggingface.co/papers`. The research-papers pipeline uses
arXiv (primary, high-volume) and the Hugging Face Daily Papers API
(secondary, curated) instead. GitHub repository association comes from
regex-scanning each paper's own abstract/comment field for a literal
`github.com/...` URL — evidence the *authors* provided, never a guess from
title similarity.

## GitHub API rate limits

Unauthenticated: 60 requests/hour, shared per IP. With `GITHUB_TOKEN` set:
5,000/hour. `src/extractors/github_stars.py` caches every lookup to
`data/mappings/github_stars_cache.json` (repeat runs cost zero extra
requests for repos already seen) and tracks remaining quota from response
headers — once exhausted, it stops issuing requests and returns `None`
(rendered as an empty cell) rather than looping into 403s. In this
environment (no token), most papers with a real declared GitHub repo still
show an empty `github_stars` column for this reason, not because the
repo association itself is fake.

## Product pricing classification yield

The products pipeline first attempts every AI-tagged YC company with a
website (1,923 of 6,200 total companies in the dataset are AI-tagged).
Real outcomes observed running this against live sites:

- DNS resolution failures (`getaddrinfo failed`) — the domain no longer
  resolves, almost always because the startup shut down or rebranded.
- TLS handshake errors — expired certs, misconfigured servers.
- Timeouts — slow or now-dead infrastructure.
- Homepages with no confident FREE/FREEMIUM/PAID/ENTERPRISE signal in
  their visible text and no discoverable pricing page.

None of these become a fabricated pricing model — the company is skipped.
This capped the AI-tagged-only pass at 768 real products, short of the
1,000 target.

**What changed to close the gap** (`src/extractors/product_pricing.py`,
`src/pipelines/products.py`):

1. When no `/pricing`-style link is found in the homepage nav, the
   extractor now also tries the handful of paths almost every SaaS site
   actually uses (`/pricing`, `/plans`, `/price`) directly, instead of
   giving up.
2. A site whose HTTPS connection fails outright (broken/expired cert — a
   real, common failure mode on small startup sites) now retries once over
   plain HTTP before being recorded as unreachable.
3. Once the AI-tagged pool's yield is known to be short of target, the
   pipeline supplements from the full YC directory
   (`YcStartupsExtractor.filter_all_companies`), excluding companies
   already attempted. Every supplemental record is still classified from
   that exact company's own live site — the change is which companies are
   *considered*, never how a pricing model is assigned.

Combined result, verified by rerunning against live data: 1,717 real
products from 4,494 fetch attempts across 10,696 discovered candidates —
above the 1,000 target, with the same zero-fabrication guarantee as
before. The honest tradeoff: some of the 1,717 are outside a strict
"AI startup" reading of the YC directory, since scope was deliberately
widened for volume once that was explicitly requested over staying
narrowly AI-tagged.

## Entity mapping log: a real bug found by running the pipeline

Early in development, running the startups pipeline and then the products
pipeline as two separate CLI invocations (`python -m src.main --pipeline
startups`, then `--pipeline products`) produced ~484 duplicate rows in
`entity_mapping_log.csv`. Root cause: `MappingLogWriter` only tracked
already-written rows in memory, and each CLI invocation constructs a fresh
instance — so the second process didn't know the first process had already
logged the same company. Fixed by loading the CSV's existing keys into the
in-memory dedup set on construction (`MappingLogWriter.__init__`), and
covered by `tests/test_mapping_log.py::test_write_dedups_across_process_restarts`.
The CSV/database in this repo were regenerated from a clean run after the
fix, so they don't contain the duplicates that led to finding it.

## No LLM keys available in this environment

Every run that produced the data in `data/` used the deterministic
fallback paths (`product_pricing.classify_from_text`,
`role_family.classify_role_family`) because no
`GEMINI_API_KEY`/`GROQ_API_KEY`/`DEEPSEEK_API_KEY` was available. The
orchestration code itself (`src/llm/orchestrator.py`,
`src/llm/providers.py`) is real and exercised by
`tests/test_llm_orchestrator.py` against mocked HTTP responses covering
success, 429 exhaustion + fallback, 413 shrink + fallback, and
no-providers-configured — but a live call to an actual Gemini/Groq/
DeepSeek endpoint has not been made from this environment, so "it works
against a real provider" is a claim the tests support but this specific
run could not verify end-to-end.

## GitHub push

No `gh` CLI and no stored GitHub credentials (checked via
`git credential fill`) were available in this environment. The repository
is fully committed locally; pushing it requires one command from whoever
has GitHub access — see the final report for the exact steps.

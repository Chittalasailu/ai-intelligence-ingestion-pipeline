# Known limitations

Consolidated detail behind the README's Limitations section — the
technical "why," for anyone extending this pipeline.

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

The products pipeline attempts every AI-tagged YC company with a website
(1,923 of 6,200 total companies in the dataset are AI-tagged). Real
outcomes observed running this against live sites:

- DNS resolution failures (`getaddrinfo failed`) — the domain no longer
  resolves, almost always because the startup shut down or rebranded.
- TLS handshake errors — expired certs, misconfigured servers.
- Timeouts — slow or now-dead infrastructure.
- Homepages with no confident FREE/FREEMIUM/PAID/ENTERPRISE signal in
  their visible text (see `src/extractors/product_pricing.py`'s keyword
  heuristic) and no discoverable `/pricing` link to fall back to.

None of these become a fabricated pricing model — the company is skipped.
This is exactly the "maximize legitimate acquisition, document the
limitation" behavior the assignment specifies for when a source can't
reach the target volume.

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

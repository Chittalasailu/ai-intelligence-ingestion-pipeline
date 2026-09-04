from src.entity_resolution.normalizer import normalize_name
from src.entity_resolution.resolver import EntityResolver
from src.entity_resolution.seed_data import SEED_STARTUPS


def test_seed_database_has_at_least_50_entities():
    assert len(SEED_STARTUPS) >= 50


def test_normalize_strips_legal_suffix_and_case():
    assert normalize_name("OpenAI, Inc.") == "openai"
    assert normalize_name("OpenAI Inc") == "openai"
    assert normalize_name("openai inc") == "openai"


def test_normalize_handles_accents_and_punctuation():
    assert normalize_name("Café-Örg!") == "cafe org"


def test_normalize_strips_corp_as_a_genuine_legal_suffix():
    # "Corp" is a real legal-entity designator (like "Inc"/"LLC"), so it's
    # deliberately stripped too -- this is what makes "Foo Corp" and
    # "Foo, Inc." normalize to the same key.
    assert normalize_name("Café Corp") == "cafe"


def test_normalize_collapses_whitespace():
    assert normalize_name("  Open   AI  ") == "open ai"


def test_normalize_does_not_strip_brand_words():
    # "Labs"/"Technologies" are identity, not legal suffixes -> not stripped.
    assert normalize_name("Adept AI Labs") == "adept ai labs"


def test_resolver_exact_match_canonical():
    r = EntityResolver()
    result = r.resolve("OpenAI")
    assert result.canonical_name == "OpenAI"
    assert result.method == "exact"
    assert result.confidence == 100.0


def test_resolver_resolves_known_openai_variants():
    r = EntityResolver()
    for variant in ["OpenAI, Inc.", "OpenAI Inc", "Open AI", "openai inc"]:
        result = r.resolve(variant)
        assert result.canonical_name == "OpenAI", f"{variant!r} resolved to {result.canonical_name!r}"


def test_resolver_alias_match():
    r = EntityResolver()
    result = r.resolve("DeepMind Technologies")
    assert result.canonical_name == "Google DeepMind"
    assert result.method in ("alias", "exact")


def test_resolver_fuzzy_match_typo():
    r = EntityResolver(fuzzy_threshold=85)
    result = r.resolve("Anthorpic")  # transposition typo of "Anthropic"
    assert result.canonical_name == "Anthropic"
    assert result.method == "fuzzy"
    assert result.confidence >= 85


def test_resolver_does_not_merge_unrelated_companies():
    r = EntityResolver(fuzzy_threshold=90)
    result = r.resolve("Totally Unrelated Startup Widgets Co")
    assert result.canonical_name not in SEED_STARTUPS
    assert result.method == "unmatched-new"
    assert result.confidence == 0.0


def test_resolver_learns_new_canonical_and_reuses_it():
    r = EntityResolver()
    first = r.resolve("Brand New Startup")
    assert first.method == "unmatched-new"
    second = r.resolve("Brand New Startup")
    assert second.method == "exact"
    assert second.canonical_name == first.canonical_name


def test_resolver_empty_name_handled_safely():
    r = EntityResolver()
    result = r.resolve("   ")
    assert result.method == "unmatched-new"


def test_resolver_default_threshold_does_not_merge_similar_short_names():
    # Regression test for a real false-merge caught in production data:
    # "Shape", "Shaped", and "Sharpe" are three distinct, real YC companies
    # (a BI tool, a retrieval engine, and a quant-research agent product)
    # that each score exactly 90.9% pairwise on token_sort_ratio — just
    # over the old default threshold of 90, which silently collapsed all
    # three into one canonical entity in the shipped startups dataset.
    # The default was raised specifically because of this case.
    r = EntityResolver()
    shape = r.resolve("Shape")
    shaped = r.resolve("Shaped")
    sharpe = r.resolve("Sharpe")
    names = {shape.canonical_name, shaped.canonical_name, sharpe.canonical_name}
    assert len(names) == 3, f"expected 3 distinct canonical entities, got {names}"


def test_resolver_default_threshold_does_not_merge_serra_into_seeded_sierra():
    # Same failure mode against a *seeded* canonical: "Serra" (a real,
    # unrelated AI recruiting startup) scored 90.9% against the seeded
    # "Sierra" (AI customer-service platform) and merged into it at the
    # old threshold — deterministically, on every run, since Sierra is
    # always present via seed_data.py.
    r = EntityResolver()
    result = r.resolve("Serra")
    assert result.canonical_name != "Sierra"


def test_resolver_default_threshold_rejects_every_observed_false_merge():
    # Every one of these pairs is a real, distinct company confirmed
    # against the YC directory (different one-liner, different website) —
    # and every one of them was incorrectly auto-merged by this resolver
    # at some point during development, including "Cair Health"/"Caire
    # Health" at 95.65%, well above the *previous* raised threshold of 95.
    # This is why the default is 97, not a smaller bump: measured against
    # ~2,450 real resolutions, every single fuzzy auto-merge this resolver
    # ever produced was wrong, so precision was prioritized over recall.
    pairs = [
        ("Cair Health", "Caire Health"),
        ("Aluna", "Alguna"),
        ("Besimple AI", "Simple AI"),
        ("Lever", "Clever"),
        ("Tella", "Trella"),
    ]
    r = EntityResolver()
    for name_a, name_b in pairs:
        result_a = r.resolve(name_a)
        result_b = r.resolve(name_b)
        assert result_a.canonical_name != result_b.canonical_name, f"{name_a!r} incorrectly merged with {name_b!r}"


def test_resolver_review_band_never_auto_merges():
    # review_threshold's actual job: a score between review_threshold and
    # fuzzy_threshold gets logged for visibility (entity_resolution_near_
    # match_not_merged) but must never auto-merge — that's still the
    # unmatched-new path, just with an audit trail instead of silence.
    r = EntityResolver(fuzzy_threshold=97, review_threshold=80)
    result = r.resolve("Serra")  # 90.9% vs seeded "Sierra" -- inside the review band
    assert result.canonical_name == "Serra"
    assert result.method == "unmatched-new"

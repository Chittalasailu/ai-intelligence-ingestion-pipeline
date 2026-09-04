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

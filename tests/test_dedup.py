from src.utils.dedup import content_hash, normalize_for_hash


def test_normalize_for_hash_collapses_whitespace_and_case():
    assert normalize_for_hash("  Hello   World  ") == "hello world"
    assert normalize_for_hash("Hello\nWorld") == "hello world"


def test_content_hash_is_deterministic():
    h1 = content_hash("Same Headline", "https://example.com/a")
    h2 = content_hash("Same Headline", "https://example.com/a")
    assert h1 == h2


def test_content_hash_ignores_case_and_whitespace_differences():
    h1 = content_hash("OpenAI Launches New Model")
    h2 = content_hash("  openai   launches new model  ")
    assert h1 == h2


def test_content_hash_differs_for_different_content():
    h1 = content_hash("Headline A")
    h2 = content_hash("Headline B")
    assert h1 != h2


def test_content_hash_is_a_valid_sha256_hex_digest():
    h = content_hash("anything")
    assert len(h) == 64
    int(h, 16)  # raises ValueError if not valid hex

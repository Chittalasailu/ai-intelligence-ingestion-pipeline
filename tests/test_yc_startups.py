from src.extractors.yc_startups import YcStartupsExtractor

_SAMPLE = [
    {"name": "Acme AI", "website": "https://acme.ai", "url": "https://yc.com/acme", "team_size": 12, "industries": ["Artificial Intelligence"], "status": "Active"},
    {"name": "Widgets Co", "website": "https://widgets.co", "url": "https://yc.com/widgets", "team_size": 5, "industries": ["B2B"], "status": "Active"},
    {"name": "Dead Startup", "website": None, "url": "https://yc.com/dead", "team_size": None, "industries": ["Machine Learning"], "status": "Inactive"},
    {"name": "", "website": "https://noname.com", "url": "https://yc.com/x", "team_size": 1, "industries": [], "status": "Active"},
]


def test_filter_ai_companies_only_returns_ai_tagged():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_ai_companies(_SAMPLE)
    names = {c.name for c in result}
    assert names == {"Acme AI", "Dead Startup"}


def test_filter_ai_companies_skips_blank_names():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_ai_companies(_SAMPLE)
    assert "" not in {c.name for c in result}


def test_filter_ai_companies_missing_website_becomes_none():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_ai_companies(_SAMPLE)
    dead = next(c for c in result if c.name == "Dead Startup")
    assert dead.website is None
    assert dead.team_size is None


def test_filter_all_companies_returns_every_named_company():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_all_companies(_SAMPLE)
    names = {c.name for c in result}
    assert names == {"Acme AI", "Widgets Co", "Dead Startup"}


def test_filter_all_companies_excludes_already_attempted_names():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_all_companies(_SAMPLE, exclude_names={"Acme AI"})
    names = {c.name for c in result}
    assert names == {"Widgets Co", "Dead Startup"}


def test_filter_all_companies_respects_limit():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_all_companies(_SAMPLE, limit=1)
    assert len(result) == 1


def test_filter_all_companies_exclusion_is_case_insensitive():
    ex = YcStartupsExtractor(http_client=None)  # type: ignore[arg-type]
    result = ex.filter_all_companies(_SAMPLE, exclude_names={"acme ai"})
    names = {c.name for c in result}
    assert "Acme AI" not in names

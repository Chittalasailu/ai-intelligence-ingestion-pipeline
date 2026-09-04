from aioresponses import aioresponses

from src.extractors.product_pricing import ProductPricingExtractor, classify_from_text, find_pricing_link
from src.schemas.models import PricingModel
from src.utils.http_client import AsyncHttpClient
from src.utils.retry import BackoffConfig


def test_classify_free_only():
    text = "Our tool is free forever, no credit card required, always free for individuals."
    assert classify_from_text(text) == PricingModel.FREE


def test_classify_freemium_free_plan_plus_price():
    text = "Start on our free plan. Upgrade to Pro for $29/month for more features."
    assert classify_from_text(text) == PricingModel.FREEMIUM


def test_classify_paid_only():
    text = "Plans start at $49/month per user. No free tier available."
    assert classify_from_text(text) == PricingModel.PAID


def test_classify_enterprise_contact_sales():
    text = "Pricing is custom for your organization. Contact sales to get a quote for enterprise plan."
    assert classify_from_text(text) == PricingModel.ENTERPRISE


def test_negation_direct_word_suppresses_free_signal():
    # "no X" immediately adjacent to the match already worked before this
    # fix and must keep working.
    text = "There is no free tier available. Plans start at $29/month."
    assert classify_from_text(text) == PricingModel.PAID


def test_negation_multi_word_phrasing_suppresses_free_signal():
    # Regression: the negation regex previously tolerated only a single word
    # between the trigger ("not") and the matched phrase ("free tier"), so
    # real phrasing like "we do not offer a free tier" fell through
    # undetected and the free signal registered anyway -- misclassifying an
    # explicitly no-free-tier page as FREEMIUM instead of PAID.
    cases = [
        "We do not have a free tier. Plans start at $29/month.",
        "We do not offer a free tier. Paid plans start at $29/month.",
        "We do not provide a free plan. Paid plans start at $29/month.",
        "This product does not include any free tier. Paid plans start at $29/month.",
    ]
    for text in cases:
        assert classify_from_text(text) == PricingModel.PAID, f"failed to suppress free signal in: {text!r}"


def test_negation_does_not_suppress_unrelated_positive_in_a_different_sentence():
    # The negation lookback must stop at a sentence break, not just fail to
    # find one within a fixed distance -- a "not" earlier in an unrelated
    # sentence must not blank out a real, separate free-tier mention.
    text = "Not sure which plan is right for you? Try our free tier today, no strings attached."
    assert classify_from_text(text) == PricingModel.FREE


def test_classify_paid_and_free_wording_unaffected_by_negation_fix():
    # Existing positive-signal behavior (no negation involved at all) must
    # be unchanged by widening the negation pattern.
    assert classify_from_text("Our tool is free forever, no credit card required.") == PricingModel.FREE
    assert classify_from_text("Our Pro plan is $29 per month, billed annually.") == PricingModel.PAID


def test_classify_ambiguous_returns_none():
    text = "Welcome to our homepage. We build great software for teams everywhere."
    assert classify_from_text(text) is None


def test_classify_does_not_guess_from_silence():
    assert classify_from_text("") is None


def test_find_pricing_link_absolute():
    html = '<html><body><nav><a href="https://example.com/pricing">Pricing</a></nav></body></html>'
    assert find_pricing_link(html, "https://example.com") == "https://example.com/pricing"


def test_find_pricing_link_relative():
    html = '<html><body><a href="/pricing">See pricing</a></body></html>'
    assert find_pricing_link(html, "https://example.com") == "https://example.com/pricing"


def test_find_pricing_link_none_when_absent():
    html = "<html><body><a href='/about'>About</a></body></html>"
    assert find_pricing_link(html, "https://example.com") is None


async def test_classify_guesses_pricing_path_when_no_nav_link():
    # Regression coverage for the extraction-yield improvement: a homepage
    # with no confident signal and no <a> pricing link should still try
    # the common /pricing path directly rather than giving up.
    home_html = "<html><body><h1>Acme</h1><p>We build great software.</p></body></html>"
    pricing_html = "<html><body><p>Free plan available. Pro is $19/month.</p></body></html>"
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=0)) as http:
        extractor = ProductPricingExtractor(http)
        with aioresponses() as m:
            m.get("https://acme.example", body=home_html, status=200)
            m.get("https://acme.example/pricing", body=pricing_html, status=200)
            result = await extractor.classify("https://acme.example")

    assert result is not None
    assert result.pricing_model == PricingModel.FREEMIUM
    assert result.fetched_url == "https://acme.example/pricing"


async def test_classify_falls_back_to_http_when_https_unreachable():
    # A real fraction of small startup sites have broken/expired TLS but
    # still serve plain HTTP — this must recover real content instead of
    # just recording the company as unreachable.
    html = "<html><body><p>Contact sales for enterprise pricing.</p></body></html>"
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=0)) as http:
        extractor = ProductPricingExtractor(http)
        with aioresponses() as m:
            m.get("https://broken-tls.example", exception=ConnectionError("ssl failure"))
            m.get("http://broken-tls.example", body=html, status=200)
            result = await extractor.classify("https://broken-tls.example")

    assert result is not None
    assert result.pricing_model == PricingModel.ENTERPRISE
    assert result.fetched_url == "http://broken-tls.example"


async def test_classify_returns_none_when_both_protocols_unreachable():
    async with AsyncHttpClient(backoff=BackoffConfig(max_retries=0)) as http:
        extractor = ProductPricingExtractor(http)
        with aioresponses() as m:
            m.get("https://dead.example", exception=ConnectionError("dns failure"))
            m.get("http://dead.example", exception=ConnectionError("dns failure"))
            result = await extractor.classify("https://dead.example")

    assert result is None

from src.extractors.product_pricing import classify_from_text, find_pricing_link
from src.schemas.models import PricingModel


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

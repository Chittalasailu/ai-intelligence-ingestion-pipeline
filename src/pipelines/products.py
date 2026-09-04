"""Products pipeline: for each AI startup (from the same YC dataset the
startups pipeline uses), fetch its live website and classify pricingModel.

Classification order: LLM (if a provider is configured) first, since it
reads nuanced pricing-page copy better than keywords -> deterministic
keyword heuristic (src/extractors/product_pricing.py) as the fallback that
always works with zero API keys. A company is skipped entirely (not
defaulted to some enum value) if neither path finds confident evidence —
see module docstring in product_pricing.py for why.
"""
from __future__ import annotations

from typing import Any, Optional

from src.crawler.base import run_bounded
from src.extractors.product_pricing import ProductPricingExtractor
from src.extractors.yc_startups import YcCompany
from src.pipelines.common import product_to_row
from src.pipelines.context import RunContext
from src.pipelines.startups import fetch_ai_companies
from src.schemas.models import PricingModel, ProductRecord, utc_now
from src.schemas.validation import validate_record
from src.storage.repository import insert_product
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

NAMESPACE = "products"

_SYSTEM_PROMPT = (
    "You classify a company's pricing model from its own homepage/pricing-page text. "
    'Respond with ONLY a JSON object: {"pricingModel": "FREE" | "FREEMIUM" | "PAID" | "ENTERPRISE"}. '
    "FREE = entirely free, no paid tier visible. FREEMIUM = a free tier/plan coexists with paid tiers. "
    "PAID = paid plans with visible pricing, no meaningful free tier. "
    "ENTERPRISE = pricing is 'contact sales' / custom quote only, no self-serve or visible price. "
    "If the text gives no real evidence either way, respond with {\"pricingModel\": null}."
)


async def _classify_via_llm(ctx: RunContext, text: str) -> Optional[PricingModel]:
    if not ctx.llm.configured_providers or not text.strip():
        return None
    result = await ctx.llm.extract_structured(_SYSTEM_PROMPT, text[:6000], target_keys=None)
    if not result.success or not result.data:
        return None
    value = result.data.get("pricingModel")
    if not value:
        return None
    try:
        return PricingModel(str(value).upper())
    except ValueError:
        logger.warning("llm_returned_invalid_pricing_enum", value=value)
        return None


async def run_products_pipeline(ctx: RunContext, target: int = 1000, companies: Optional[list[YcCompany]] = None) -> list[dict[str, Any]]:
    source_cfgs = ctx.settings.source_group("products")
    product_cfg = next((s for s in source_cfgs if s["adapter"] == "yc_products"), {})
    source_name = product_cfg.get("name", "Company website (pricing page)")

    ai_companies = companies if companies is not None else await fetch_ai_companies(ctx, target)
    ai_companies = [c for c in ai_companies if c.website]

    pricing_extractor = ProductPricingExtractor(ctx.http_client)

    async def _process(company: YcCompany) -> dict[str, Any] | None:
        dedup_id = company.name.strip().lower()
        if await ctx.checkpoint.has_seen(NAMESPACE, dedup_id):
            ctx.stats.incr("duplicates_removed")
            return None

        ctx.stats.incr("records_discovered")
        classification = await pricing_extractor.classify(company.website)  # type: ignore[arg-type]
        ctx.stats.incr("records_fetched")

        pricing_model: Optional[PricingModel] = None
        evidence_url = company.website
        if classification is not None:
            ctx.stats.incr("records_parsed")
            llm_result = await _classify_via_llm(ctx, classification.evidence)
            pricing_model = llm_result or classification.pricing_model
            evidence_url = classification.fetched_url
        else:
            return None  # no confident signal from either the homepage or a discovered pricing page

        resolution = ctx.resolver.resolve(company.name)
        await ctx.record_entity_mapping(company.name, resolution, evidence_url)

        payload = {
            "schemaVersion": "1.0",
            "recordType": "PRODUCT",
            "source": {"name": source_name, "url": evidence_url},
            "content": {"startupName": resolution.canonical_name, "pricingModel": pricing_model.value},
            "collectedAt": utc_now().isoformat(),
        }
        record = validate_record(ProductRecord, payload, ctx.stats, ctx.rejection_log, "PRODUCT", evidence_url)
        if record is None:
            return None

        async with ctx.session_factory() as session:
            inserted = await insert_product(session, record, resolution.canonical_name)
            if not inserted:
                ctx.stats.incr("duplicates_removed")
                return None
        await ctx.checkpoint.mark_seen(NAMESPACE, dedup_id, utc_now().isoformat())
        return product_to_row(record)

    results = await run_bounded(ai_companies, _process, ctx.settings.max_concurrency)
    rows = [r for r in results if r]
    logger.info("products_pipeline_done", collected=len(rows), target=target, candidates=len(ai_companies))
    return rows

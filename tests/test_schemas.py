import pytest
from pydantic import ValidationError

from src.schemas.models import (
    JobRecord,
    NewsRecord,
    PricingModel,
    ProductRecord,
    ResearchPaperRecord,
    StartupRecord,
)
from src.schemas.validation import QualityStats, RejectionLog, is_valid_github_evidence, validate_record


def _valid_startup_payload():
    return {
        "schemaVersion": "1.0",
        "recordType": "STARTUP",
        "source": {"name": "YC OSS", "url": "https://example.com/co"},
        "content": {"entityName": "Acme AI", "data": {"employeeCount": 42}},
        "collectedAt": "2026-09-04T10:00:00Z",
    }


def test_startup_record_valid():
    r = StartupRecord.model_validate(_valid_startup_payload())
    assert r.content.entityName == "Acme AI"
    assert r.content.data.employeeCount == 42


def test_startup_record_missing_employee_count_is_none_not_zero():
    payload = _valid_startup_payload()
    payload["content"]["data"] = {}
    r = StartupRecord.model_validate(payload)
    assert r.content.data.employeeCount is None


def test_startup_record_rejects_bad_url():
    payload = _valid_startup_payload()
    payload["source"]["url"] = "not-a-url"
    with pytest.raises(ValidationError):
        StartupRecord.model_validate(payload)


def test_startup_record_rejects_wrong_record_type():
    payload = _valid_startup_payload()
    payload["recordType"] = "PRODUCT"
    with pytest.raises(ValidationError):
        StartupRecord.model_validate(payload)


def test_product_record_enum_valid_values():
    for value in ["FREE", "FREEMIUM", "PAID", "ENTERPRISE"]:
        payload = {
            "schemaVersion": "1.0",
            "recordType": "PRODUCT",
            "source": {"name": "co website", "url": "https://example.com"},
            "content": {"startupName": "Acme AI", "pricingModel": value},
            "collectedAt": "2026-09-04T10:00:00Z",
        }
        r = ProductRecord.model_validate(payload)
        assert r.content.pricingModel == PricingModel(value)


def test_product_record_rejects_invalid_pricing_enum():
    payload = {
        "schemaVersion": "1.0",
        "recordType": "PRODUCT",
        "source": {"name": "co website", "url": "https://example.com"},
        "content": {"startupName": "Acme AI", "pricingModel": "SUBSCRIPTION"},
        "collectedAt": "2026-09-04T10:00:00Z",
    }
    with pytest.raises(ValidationError):
        ProductRecord.model_validate(payload)


def test_research_paper_record_allows_null_github_fields():
    payload = {
        "schemaVersion": "1.0",
        "recordType": "RESEARCH_PAPER",
        "source": {"name": "arXiv API", "url": "https://arxiv.org/abs/2508.12345"},
        "content": {
            "title": "A Great Paper",
            "authors": ["Jane Doe"],
            "paper_url": "https://arxiv.org/abs/2508.12345",
            "github_url": None,
            "github_stars": None,
            "published_date": "2026-09-01T00:00:00Z",
        },
        "collectedAt": "2026-09-04T10:00:00Z",
    }
    r = ResearchPaperRecord.model_validate(payload)
    assert r.content.github_url is None
    assert r.content.github_stars is None


def test_job_record_valid():
    payload = {
        "schemaVersion": "1.0",
        "recordType": "JOB",
        "source": {"name": "RemoteOK", "url": "https://remoteok.com/api"},
        "content": {
            "company": "Anthropic",
            "date": "2026-09-04T08:00:00Z",
            "is_remote": True,
            "role_family": "Engineering",
            "title": "ML Engineer",
        },
        "collectedAt": "2026-09-04T10:00:00Z",
    }
    r = JobRecord.model_validate(payload)
    assert r.content.is_remote is True


def test_news_record_valid():
    payload = {
        "schemaVersion": "1.0",
        "recordType": "NEWS",
        "source": {"name": "TechCrunch AI", "url": "https://techcrunch.com/foo"},
        "content": {
            "headline": "Big AI news",
            "date": "2026-09-04T09:00:00Z",
            "url": "https://techcrunch.com/foo",
        },
        "collectedAt": "2026-09-04T10:00:00Z",
    }
    r = NewsRecord.model_validate(payload)
    assert r.content.headline == "Big AI news"


def test_validate_record_rejects_and_logs(tmp_path):
    stats = QualityStats()
    rejection_log = RejectionLog(tmp_path / "rejected.jsonl")
    bad_payload = _valid_startup_payload()
    bad_payload["source"]["url"] = "not-a-url"

    result = validate_record(StartupRecord, bad_payload, stats, rejection_log, "STARTUP", "not-a-url")

    assert result is None
    assert stats.records_rejected == 1
    assert (tmp_path / "rejected.jsonl").exists()
    content = (tmp_path / "rejected.jsonl").read_text(encoding="utf-8")
    assert "schema_validation_error" in content


def test_validate_record_accepts_and_counts(tmp_path):
    stats = QualityStats()
    rejection_log = RejectionLog(tmp_path / "rejected.jsonl")
    result = validate_record(StartupRecord, _valid_startup_payload(), stats, rejection_log, "STARTUP")
    assert result is not None
    assert stats.records_validated == 1


def test_quality_stats_as_dict_does_not_crash_on_lock_field():
    # Regression test: dataclasses.asdict() deep-copies every field,
    # including the internal threading.Lock, which raises TypeError. Caught
    # by actually running the pipeline end-to-end, not by inspection.
    stats = QualityStats()
    stats.incr("records_discovered", 5)
    d = stats.as_dict()
    assert d["records_discovered"] == 5
    assert "_lock" not in d


def test_github_evidence_requires_verbatim_match():
    assert is_valid_github_evidence("https://github.com/foo/bar", "code at https://github.com/foo/bar see readme") is True
    assert is_valid_github_evidence("https://github.com/foo/bar", "no repo mentioned here") is False
    assert is_valid_github_evidence(None, "some text") is False
    assert is_valid_github_evidence("https://github.com/foo/bar", None) is False

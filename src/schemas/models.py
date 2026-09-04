"""Canonical pydantic schemas for every record type the pipeline emits.

These mirror the schemas in the assignment spec exactly (field names, enum
values, nesting). Anything that fails validation here never reaches storage
or export — see src/schemas/validation.py for the reject-and-log wrapper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class Source(BaseModel):
    name: str
    url: HttpUrl


class StartupData(BaseModel):
    employeeCount: Optional[int] = None


class StartupContent(BaseModel):
    entityName: str
    data: StartupData


class StartupRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = Field(default="STARTUP", frozen=True)
    source: Source
    content: StartupContent
    collectedAt: datetime

    @field_validator("recordType")
    @classmethod
    def _fixed_type(cls, v: str) -> str:
        if v != "STARTUP":
            raise ValueError("recordType must be STARTUP")
        return v


class ProductContent(BaseModel):
    startupName: str
    pricingModel: PricingModel


class ProductRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = Field(default="PRODUCT", frozen=True)
    source: Source
    content: ProductContent
    collectedAt: datetime

    @field_validator("recordType")
    @classmethod
    def _fixed_type(cls, v: str) -> str:
        if v != "PRODUCT":
            raise ValueError("recordType must be PRODUCT")
        return v


class ResearchPaperContent(BaseModel):
    title: str
    authors: list[str]
    paper_url: HttpUrl
    github_url: Optional[HttpUrl] = None
    github_stars: Optional[int] = None
    published_date: datetime


class ResearchPaperRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = Field(default="RESEARCH_PAPER", frozen=True)
    source: Source
    content: ResearchPaperContent
    collectedAt: datetime

    @field_validator("recordType")
    @classmethod
    def _fixed_type(cls, v: str) -> str:
        if v != "RESEARCH_PAPER":
            raise ValueError("recordType must be RESEARCH_PAPER")
        return v


class JobContent(BaseModel):
    company: str
    date: datetime
    is_remote: bool
    role_family: str
    title: Optional[str] = None
    url: Optional[HttpUrl] = None


class JobRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = Field(default="JOB", frozen=True)
    source: Source
    content: JobContent
    collectedAt: datetime

    @field_validator("recordType")
    @classmethod
    def _fixed_type(cls, v: str) -> str:
        if v != "JOB":
            raise ValueError("recordType must be JOB")
        return v


class NewsContent(BaseModel):
    headline: str
    date: datetime
    summary: Optional[str] = None
    full_text: Optional[str] = None
    url: HttpUrl


class NewsRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: str = Field(default="NEWS", frozen=True)
    source: Source
    content: NewsContent
    collectedAt: datetime

    @field_validator("recordType")
    @classmethod
    def _fixed_type(cls, v: str) -> str:
        if v != "NEWS":
            raise ValueError("recordType must be NEWS")
        return v


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

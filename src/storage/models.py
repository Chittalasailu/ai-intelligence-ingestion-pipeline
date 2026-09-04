"""SQLAlchemy 2.0 ORM models. Written against the ORM (not raw SQL) so the
same models run unmodified against SQLite (zero-setup demo default) or
Postgres (DATABASE_URL=postgresql+asyncpg://... for production) — see
docs/ARCHITECTURE for why Postgres is the recommended production target.

One table per record type, mirroring the assignment's JSON schemas field
for field, plus a `dedup_key` unique constraint per table that is exactly
the content-hash used by src/utils/dedup.py — duplicate inserts are
rejected at the database layer as a second line of defense behind the
in-memory/checkpoint dedup the crawler already does.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StartupORM(Base):
    __tablename__ = "startups"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_startups_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    record_type: Mapped[str] = mapped_column(String(32), default="STARTUP")
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(1024))
    entity_name: Mapped[str] = mapped_column(String(512))
    canonical_name: Mapped[str] = mapped_column(String(512), index=True)
    employee_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductORM(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_products_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    record_type: Mapped[str] = mapped_column(String(32), default="PRODUCT")
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(1024))
    startup_name: Mapped[str] = mapped_column(String(512))
    canonical_name: Mapped[str] = mapped_column(String(512), index=True)
    pricing_model: Mapped[str] = mapped_column(String(32))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchPaperORM(Base):
    __tablename__ = "research_papers"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_papers_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    record_type: Mapped[str] = mapped_column(String(32), default="RESEARCH_PAPER")
    source_name: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    title: Mapped[str] = mapped_column(String(1024))
    authors: Mapped[list] = mapped_column(JSON)
    paper_url: Mapped[str] = mapped_column(String(1024))
    github_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    github_stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    published_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobORM(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_jobs_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    record_type: Mapped[str] = mapped_column(String(32), default="JOB")
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(1024))
    company: Mapped[str] = mapped_column(String(512))
    canonical_company: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512))
    role_family: Mapped[str] = mapped_column(String(64))
    is_remote: Mapped[bool] = mapped_column(Boolean)
    job_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsORM(Base):
    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_news_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    record_type: Mapped[str] = mapped_column(String(32), default="NEWS")
    source_name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(1024))
    headline: Mapped[str] = mapped_column(String(1024))
    summary: Mapped[str] = mapped_column(String(4096), default="")
    news_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EntityMappingORM(Base):
    __tablename__ = "entity_mappings"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_entity_mappings_dedup_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    raw_name: Mapped[str] = mapped_column(String(512))
    canonical_name: Mapped[str] = mapped_column(String(512), index=True)
    method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))

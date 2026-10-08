"""Persistence model (section 19). Monetary values are stored as whole INR in BigInteger columns."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, **kw)


# ---------------------------------------------------------------- auth
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin | analyst | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- portals
class Portal(Base):
    __tablename__ = "portals"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    connector: Mapped[str] = mapped_column(String(50))  # key in connectors.registry
    base_url: Mapped[str] = mapped_column(String(500))
    acquisition_method: Mapped[str] = mapped_column(String(30))  # API | FEED | HTML | BROWSER | BROWSER_AUTH | MANUAL
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_minutes: Mapped[int] = mapped_column(Integer, default=45)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    last_error: Mapped[str | None] = mapped_column(Text)
    blocker: Mapped[str | None] = mapped_column(Text)  # e.g. "Detail pages are CAPTCHA-gated"


class PortalCredentialMetadata(Base):
    """Describes HOW to authenticate — never the raw password. Secrets live in env vars / a secret
    store; `encrypted_secret` (Fernet) is only used when an admin explicitly stores one."""
    __tablename__ = "portal_credentials_metadata"
    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"))
    auth_type: Mapped[str] = mapped_column(String(30))  # NONE | FORM_LOGIN | API_KEY | DSC
    username_env_var: Mapped[str | None] = mapped_column(String(100))
    secret_env_var: Mapped[str | None] = mapped_column(String(100))
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    authorized_by: Mapped[str | None] = mapped_column(String(200))  # who confirmed the company may use it
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _ts(onupdate=utcnow)


# ---------------------------------------------------------------- tenders
class Tender(Base):
    __tablename__ = "tenders"
    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id"))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)

    portal_tender_id: Mapped[str | None] = mapped_column(String(200), index=True)
    reference_number: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(String(500))
    department: Mapped[str | None] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(200))
    tender_type: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    opening_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tender_value_inr: Mapped[int | None] = mapped_column(BigInteger)
    emd_inr: Mapped[int | None] = mapped_column(BigInteger)
    source_url: Mapped[str | None] = mapped_column(Text)
    contact_info: Mapped[str | None] = mapped_column(Text)
    corrigendum: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # pipeline state
    pipeline_status: Mapped[str] = mapped_column(String(30), default="DISCOVERED", index=True)
    documents_status: Mapped[str] = mapped_column(String(40), default="NONE")
    blocker_note: Mapped[str | None] = mapped_column(Text)
    analysis_mode: Mapped[str | None] = mapped_column(String(20))  # LLM | RULES_ONLY
    model_version: Mapped[str | None] = mapped_column(String(100))

    # decision
    decision: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    opportunity_type: Mapped[str] = mapped_column(String(20), default="UNKNOWN", index=True)
    priority: Mapped[str | None] = mapped_column(String(10), index=True)
    score: Mapped[float | None] = mapped_column(Float, index=True)
    match_confidence: Mapped[int | None] = mapped_column(Integer)
    primary_capability: Mapped[str | None] = mapped_column(String(200))
    matched_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    matched_products: Mapped[list[str]] = mapped_column(JSON, default=list)
    service_value_inr: Mapped[int | None] = mapped_column(BigInteger)
    product_value_inr: Mapped[int | None] = mapped_column(BigInteger)
    value_analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    relevance_reason: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # field -> {value, confidence, evidence}

    first_seen_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts(onupdate=utcnow)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    portal: Mapped[Portal] = relationship()
    documents: Mapped[list[TenderDocument]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    matches: Mapped[list[TenderMatch]] = relationship(back_populates="tender", cascade="all, delete-orphan")


class TenderVersion(Base):
    __tablename__ = "tender_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    __table_args__ = (UniqueConstraint("tender_id", "version"),)


class TenderDocument(Base):
    __tablename__ = "tender_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(20), default="PORTAL")  # PORTAL | UPLOAD | ZIP_MEMBER
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    storage_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    extraction_method: Mapped[str | None] = mapped_column(String(30))
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    sections: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = _ts()
    tender: Mapped[Tender] = relationship(back_populates="documents")


class ExtractedRequirement(Base):
    """Every extracted field / requirement / component, with confidence and evidence (section 11)."""
    __tablename__ = "extracted_requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    tender_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # FIELD | REQUIREMENT | COMPONENT
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[str | None] = mapped_column(Text)
    component_kind: Mapped[str | None] = mapped_column(String(20))  # SERVICE | PRODUCT
    value_inr: Mapped[int | None] = mapped_column(BigInteger)
    confidence: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # PORTAL | DETERMINISTIC | LLM
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- catalog (mirrors catalog.yaml)
class CapabilityRow(Base):
    __tablename__ = "capabilities"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category_code: Mapped[str] = mapped_column(String(5))
    category_name: Mapped[str] = mapped_column(String(200))
    offering: Mapped[str] = mapped_column(String(20))


class CapabilitySynonym(Base):
    __tablename__ = "capability_synonyms"
    id: Mapped[int] = mapped_column(primary_key=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id", ondelete="CASCADE"), index=True)
    pattern: Mapped[str] = mapped_column(Text)
    match_type: Mapped[str] = mapped_column(String(10))
    sub_capability: Mapped[str | None] = mapped_column(String(200))
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)


class Oem(Base):
    __tablename__ = "oems"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)


class ProductRow(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id"))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)


# ---------------------------------------------------------------- analysis results
class TenderMatch(Base):
    __tablename__ = "tender_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    tender_version: Mapped[int] = mapped_column(Integer)
    capability_id: Mapped[str] = mapped_column(String(60))
    capability_name: Mapped[str] = mapped_column(String(200))
    sub_capability: Mapped[str | None] = mapped_column(String(200))
    product_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    explicit_oem: Mapped[bool] = mapped_column(Boolean, default=False)
    match_type: Mapped[str] = mapped_column(String(10))  # DIRECT | SEMANTIC | ADJACENT
    confidence: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # LEXICON | LLM
    tender: Mapped[Tender] = relationship(back_populates="matches")


class ScoreRow(Base):
    __tablename__ = "scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    tender_version: Mapped[int] = mapped_column(Integer)
    total: Mapped[float] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(10))
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON)
    weights: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id", ondelete="SET NULL"), index=True)
    tender_version: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # IMMEDIATE | DIGEST
    priority: Mapped[str | None] = mapped_column(String(10))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    recipients: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")  # QUEUED | SENT | FAILED | NOT_CONFIGURED
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingJob(Base):
    """Durable job queue (claimed with SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL)."""
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED | RUNNING | DONE | FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = _ts()
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"), Index("ix_jobs_dedupe", "dedupe_key", "status"))


class AuditLog(Base):
    """Section 21: every decision with its full context."""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int | None] = mapped_column(ForeignKey("tenders.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(320), default="system")
    action: Mapped[str] = mapped_column(String(60))
    decision: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _ts(index=True)


class RejectionReason(Base):
    __tablename__ = "rejection_reasons"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    tender_version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(30))  # PREFILTER | COMMERCIAL | RELEVANCE | SCORE
    code: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class ManualReview(Base):
    __tablename__ = "manual_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    tender_version: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | RESOLVED | SUPERSEDED
    resolution: Mapped[str | None] = mapped_column(String(20))  # ACCEPTED | REJECTED
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

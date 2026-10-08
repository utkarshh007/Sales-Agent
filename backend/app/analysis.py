"""Portal-agnostic analysis data types shared by the rule engine, LLM worker, matcher and scorer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

UNKNOWN = "UNKNOWN"

# Section 11 — every field the extractor must return (value | UNKNOWN, confidence, evidence).
FIELD_NAMES = [
    "tender_title", "tender_reference", "procuring_organization", "department", "tender_type",
    "publication_date", "closing_date", "tender_value", "emd", "estimated_contract_value",
    "contract_duration", "location", "eligibility_criteria", "technical_requirements", "scope_of_work",
    "required_products", "required_oems", "required_certifications", "required_manpower",
    "experience_requirements", "turnover_requirements", "security_requirements",
    "implementation_requirements", "support_amc_requirements", "service_components",
    "product_oem_components", "important_deadlines", "mandatory_documents",
]


@dataclass
class FieldValue:
    value: str = UNKNOWN
    confidence: int = 0
    evidence: str = ""
    source: str = "LLM"  # PORTAL | DETERMINISTIC | LLM


@dataclass
class Requirement:
    text: str
    kind: str  # SERVICE | PRODUCT | OTHER
    capability_ids: list[str]
    sub_capability: str
    match_type: str  # DIRECT | SEMANTIC | ADJACENT | NONE
    confidence: int
    evidence: str
    explanation: str
    explicit_product_ids: list[str] = field(default_factory=list)
    other_oems_named: list[str] = field(default_factory=list)


@dataclass
class Component:
    description: str
    kind: str  # SERVICE | PRODUCT
    value_inr: int | None
    evidence: str = ""


@dataclass
class CapabilityMatch:
    capability_id: str
    capability_name: str
    offering: str
    sub_capability: str
    match_type: str
    confidence: int
    evidence: str
    explanation: str
    product_ids: list[str] = field(default_factory=list)  # supported portfolio, explicitly named first
    explicit_product_ids: list[str] = field(default_factory=list)  # products the tender itself names
    source: str = "LEXICON"  # LEXICON | LLM

    @property
    def explicit_oem(self) -> bool:
        return bool(self.explicit_product_ids)


@dataclass
class TenderContext:
    """Everything the engine knows about a tender, independent of the portal it came from."""
    title: str
    organization: str | None = None
    reference_number: str | None = None
    closing_at: datetime | None = None
    published_at: datetime | None = None
    portal_value_inr: int | None = None
    portal_emd_inr: int | None = None
    location: str | None = None
    category: str | None = None
    document_text: str = ""  # normalised, concatenated extracted text (may be empty)
    llm_context: str = ""  # targeted excerpts sent to the LLM
    llm_context_truncated: bool = False
    has_documents: bool = False
    has_portal_detail: bool = False  # structured detail page read from the portal (value, category, description)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Analysis:
    mode: str  # LLM | RULES_ONLY
    model_version: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    requirements: list[Requirement] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    total_value_inr: int | None = None
    total_value_evidence: str = ""
    eligibility_assessment: str = UNKNOWN  # FEASIBLE | PARTIAL | INFEASIBLE | UNKNOWN
    eligibility_issues: list[str] = field(default_factory=list)
    strategic_relevance: str = UNKNOWN  # HIGH | MEDIUM | LOW | UNKNOWN
    generic_security_only: bool = False
    summary: str = ""
    recommendation: str = ""
    risks: list[str] = field(default_factory=list)
    refused: bool = False
    notes: list[str] = field(default_factory=list)

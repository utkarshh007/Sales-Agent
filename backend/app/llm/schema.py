"""JSON schema for structured LLM output. Capability and product IDs are enums generated from the
catalog, so the model physically cannot invent a capability or OEM the company does not offer."""
from __future__ import annotations

from app.analysis import FIELD_NAMES
from app.catalog import Catalog


def _nullable_number() -> dict:
    return {"anyOf": [{"type": "number"}, {"type": "null"}]}


def analysis_schema(catalog: Catalog) -> dict:
    cap_ids = catalog.capability_ids()
    prod_ids = catalog.product_ids()
    field_obj = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": FIELD_NAMES},
            "value": {"type": "string", "description": "Exact value from the tender, or UNKNOWN"},
            "confidence": {"type": "integer", "description": "0-100"},
            "evidence": {"type": "string", "description": "Short verbatim quote with document/page/section reference, or empty"},
        },
        "required": ["name", "value", "confidence", "evidence"],
        "additionalProperties": False,
    }
    requirement = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "kind": {"type": "string", "enum": ["SERVICE", "PRODUCT", "OTHER"]},
            "capability_ids": {"type": "array", "items": {"type": "string", "enum": cap_ids}},
            "sub_capability": {"type": "string"},
            "match_type": {"type": "string", "enum": ["DIRECT", "SEMANTIC", "ADJACENT", "NONE"]},
            "confidence": {"type": "integer", "description": "0-100"},
            "evidence": {"type": "string"},
            "explanation": {"type": "string"},
            "explicit_product_ids": {"type": "array", "items": {"type": "string", "enum": prod_ids}},
            "other_oems_named": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "kind", "capability_ids", "sub_capability", "match_type", "confidence",
                     "evidence", "explanation", "explicit_product_ids", "other_oems_named"],
        "additionalProperties": False,
    }
    component = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "kind": {"type": "string", "enum": ["SERVICE", "PRODUCT"]},
            "value_inr": _nullable_number(),
            "evidence": {"type": "string"},
        },
        "required": ["description", "kind", "value_inr", "evidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "fields": {"type": "array", "items": field_obj},
            "requirements": {"type": "array", "items": requirement},
            "components": {"type": "array", "items": component},
            "total_value_inr": _nullable_number(),
            "total_value_evidence": {"type": "string"},
            "eligibility_assessment": {"type": "string", "enum": ["FEASIBLE", "PARTIAL", "INFEASIBLE", "UNKNOWN"]},
            "eligibility_issues": {"type": "array", "items": {"type": "string"}},
            "strategic_relevance": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]},
            "generic_security_only": {"type": "boolean"},
            "summary": {"type": "string"},
            "recommendation": {"type": "string"},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fields", "requirements", "components", "total_value_inr", "total_value_evidence",
                     "eligibility_assessment", "eligibility_issues", "strategic_relevance",
                     "generic_security_only", "summary", "recommendation", "risks"],
        "additionalProperties": False,
    }

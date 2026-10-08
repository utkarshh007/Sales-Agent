"""LLM plumbing, without network: request shape, schema constraints, parsing, refusal handling."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.analysis import FIELD_NAMES
from app.engine import MANUAL_REVIEW, evaluate
from app.llm.analyzer import ClaudeAnalyzer, LLMUnavailable, parse_analysis
from app.llm.prompts import system_prompt
from app.llm.schema import analysis_schema
from tests.conftest import NOW, ctx

SAMPLE = {
    "fields": [{"name": "tender_value", "value": "₹2.4 crore", "confidence": 90, "evidence": "NIT p1: Estimated cost ₹2.4 Cr"},
               {"name": "emd", "value": "", "confidence": 50, "evidence": ""}],
    "requirements": [{"text": "Enterprise SIEM with correlation", "kind": "PRODUCT", "capability_ids": ["prd_siem", "not_a_cap"],
                      "sub_capability": "SIEM", "match_type": "SEMANTIC", "confidence": 140, "evidence": "Sec 3.1",
                      "explanation": "Centralised correlation = SIEM", "explicit_product_ids": ["qradar"],
                      "other_oems_named": ["Securonix"]}],
    "components": [{"description": "SIEM licences", "kind": "PRODUCT", "value_inr": 24000000, "evidence": "BOQ 1"}],
    "total_value_inr": 24000000, "total_value_evidence": "NIT p1", "eligibility_assessment": "PARTIAL",
    "eligibility_issues": ["Turnover ₹50 Cr"], "strategic_relevance": "HIGH", "generic_security_only": False,
    "summary": "SIEM procurement", "recommendation": "Bid with QRadar", "risks": ["Securonix named"],
}


class FakeMessages:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def fake_client(resp):
    msgs = FakeMessages(resp)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def response(text=None, stop="end_turn"):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(content=content, stop_reason=stop, model="claude-opus-5-5", stop_details=None,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))


def test_schema_constrains_ids_to_catalog(catalog):
    schema = analysis_schema(catalog)
    req = schema["properties"]["requirements"]["items"]["properties"]
    assert set(req["capability_ids"]["items"]["enum"]) == set(catalog.capabilities)
    assert set(req["explicit_product_ids"]["items"]["enum"]) == set(catalog.products)
    assert schema["properties"]["fields"]["items"]["properties"]["name"]["enum"] == FIELD_NAMES


def test_system_prompt_is_deterministic_for_caching(catalog):
    assert system_prompt(catalog) == system_prompt(catalog)
    assert "prd_siem" in system_prompt(catalog) and "Splunk" in system_prompt(catalog)


def test_request_shape(settings, catalog):
    client, msgs = fake_client(response(json.dumps(SAMPLE)))
    a = ClaudeAnalyzer(settings, catalog, client=client).analyze(ctx("SIEM tender", "doc"), {})
    k = msgs.kwargs
    assert k["model"] == settings.LLM_MODEL
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert k["output_config"]["format"]["type"] == "json_schema"
    assert k["thinking"] == {"type": "adaptive"}
    assert k["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in k["betas"]
    assert "<tender_documents>" in k["messages"][0]["content"]
    assert a.mode == "LLM" and a.model_version == "claude-opus-5-5"


def test_parse_clamps_and_fills_unknowns():
    a = parse_analysis(SAMPLE, "m")
    assert a.requirements[0].confidence == 100
    assert a.fields["emd"].value == "UNKNOWN" and a.fields["emd"].confidence == 0
    assert a.fields["turnover_requirements"].value == "UNKNOWN"
    assert set(a.fields) == set(FIELD_NAMES)


def test_matching_drops_ids_outside_catalog_and_flags_foreign_oems(settings, catalog):
    client, _ = fake_client(response(json.dumps(SAMPLE)))
    d = evaluate(ctx("Enterprise SIEM solution procurement", "Estimated cost ₹2.4 crore"), catalog, settings,
                 ClaudeAnalyzer(settings, catalog, client=client), now=NOW)
    assert {m.capability_id for m in d.matches} == {"prd_siem"}
    siem = d.matches[0]
    assert siem.explicit_product_ids == ["qradar"]
    assert siem.source == "LLM+LEXICON" and "Centralised correlation = SIEM" in siem.explanation
    assert "NON_PORTFOLIO_OEM_SPECIFIED" in d.flags
    assert d.opportunity_type == "OEM" and d.status == "ACCEPTED"


def test_refusal_routes_to_manual_review_with_rules_fallback(settings, catalog):
    client, _ = fake_client(response(stop="refusal"))
    d = evaluate(ctx("Procurement of Cobalt Strike licences for red team", ""), catalog, settings,
                 ClaudeAnalyzer(settings, catalog, client=client), now=NOW)
    assert d.status == MANUAL_REVIEW
    assert d.reviews[0][0] == "LLM_DECLINED"
    assert d.analysis.mode == "RULES_ONLY"
    assert any(m.capability_id == "prd_pentest_tools" for m in d.matches)


def test_truncated_output_is_retryable(settings, catalog):
    client, _ = fake_client(response('{"fields": [', stop="max_tokens"))
    with pytest.raises(LLMUnavailable):
        ClaudeAnalyzer(settings, catalog, client=client).analyze(ctx("x"), {})


def test_llm_saying_unrelated_rejects(settings, catalog):
    data = dict(SAMPLE, requirements=[dict(SAMPLE["requirements"][0], match_type="NONE", capability_ids=[],
                                           explicit_product_ids=[], other_oems_named=[])], components=[])
    client, _ = fake_client(response(json.dumps(data)))
    # title has only a generic signal; LLM finds no catalog requirement
    d = evaluate(ctx("Hiring of cyber security manpower for helpdesk", "doc"), catalog, settings,
                 ClaudeAnalyzer(settings, catalog, client=client), now=NOW)
    assert d.status == "REJECTED" and d.opportunity_type == "UNRELATED"

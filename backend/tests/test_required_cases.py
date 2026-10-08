"""Section 25 — the twelve required test cases, plus the rule variants they imply."""
from __future__ import annotations

import pytest

from app.analysis import Component, Requirement
from app.engine import ACCEPTED, MANUAL_REVIEW, REJECTED, evaluate
from app.llm.analyzer import RulesAnalyzer
from app.matching import build_matches
from tests.conftest import NOW, StubAnalyzer, ctx, llm_analysis


def run(c, settings, catalog, analyzer=None):
    return evaluate(c, catalog, settings, analyzer or RulesAnalyzer(settings, catalog), now=NOW)


# ---------------------------------------------------------------- Test 1-3: service value rule
def test_01_red_team_18_lakh_service_accept(settings, catalog):
    d = run(ctx("Red Team Assessment of IT infrastructure",
                "Scope of work: conduct a red team assessment. Estimated cost: Rs. 18,00,000 (Rupees Eighteen Lakh)."),
            settings, catalog)
    assert d.opportunity_type == "SERVICE"
    assert d.commercial.status == "ACCEPT"
    assert d.commercial.service_value_inr == 1_800_000
    assert d.status == ACCEPTED
    assert d.matches[0].capability_id == "svc_redteam"


def test_02_appsec_30_lakh_service_accept_at_cap(settings, catalog):
    d = run(ctx("Application Security Assessment of web portals",
                "Estimated value of the tender: ₹30 Lakh. Application security testing of 12 web applications."),
            settings, catalog)
    assert d.opportunity_type == "SERVICE"
    assert d.commercial.service_value_inr == 3_000_000
    assert d.commercial.status == "ACCEPT"
    assert d.status == ACCEPTED


def test_03_appsec_31_lakh_service_reject(settings, catalog):
    analyzer = StubAnalyzer(llm_analysis([], []))
    d = run(ctx("Application Security Assessment of web portals",
                "Estimated value of the tender: Rs. 31,00,000. Application security testing of web applications."),
            settings, catalog, analyzer)
    assert d.opportunity_type == "SERVICE"
    assert d.status == REJECTED
    assert d.rejections[0][1] == "SERVICE_OVER_CAP"
    assert analyzer.calls == 0, "deterministic rejection must not spend LLM tokens"


def test_03b_service_over_cap_rejected_after_llm_classification(settings, catalog):
    """Value only discoverable by the LLM (not plainly labelled) still triggers the service cap."""
    analysis = llm_analysis(
        [Requirement("Web application VAPT", "SERVICE", ["svc_appsec"], "Web Application Penetration Testing",
                     "SEMANTIC", 90, "VAPT of 40 applications", "maps to AppSec")],
        [Component("VAPT services, 3 years", "SERVICE", 3_500_000, "BOQ line 1: 35,00,000")], total=3_500_000)
    d = run(ctx("Engagement of agency for VAPT", "BOQ attached"), settings, catalog, StubAnalyzer(analysis))
    assert d.opportunity_type == "SERVICE"
    assert d.status == REJECTED and d.rejections[0][1] == "SERVICE_OVER_CAP"


# ---------------------------------------------------------------- Test 4-5: OEM has no cap
def test_04_splunk_siem_2_crore_oem_accept(settings, catalog):
    d = run(ctx("Supply, installation and commissioning of Splunk Enterprise Security SIEM solution",
                "Estimated cost: Rs. 2 Crore. Security information and event management platform with licences for 3 years."),
            settings, catalog)
    assert d.opportunity_type == "OEM"
    assert d.commercial.status == "ACCEPT"
    assert d.commercial.total_value_inr == 20_000_000
    assert d.status == ACCEPTED
    siem = next(m for m in d.matches if m.capability_id == "prd_siem")
    assert siem.explicit_oem and siem.product_ids[0] == "splunk"
    assert d.priority in ("HOT", "HIGH")


def test_05_pam_5_crore_oem_accept(settings, catalog):
    d = run(ctx("Procurement of Privileged Access Management solution with licences",
                "Estimated cost: ₹5 crore. Privileged access management with session recording for 5000 users."),
            settings, catalog)
    assert d.opportunity_type == "OEM"
    assert d.commercial.status == "ACCEPT"
    assert d.commercial.total_value_inr == 50_000_000
    assert d.status == ACCEPTED


def test_oem_value_limit_is_configurable(settings, catalog):
    settings.OEM_VALUE_LIMIT_ENABLED, settings.OEM_MAX_VALUE_INR = True, 10_000_000
    d = run(ctx("Procurement of Privileged Access Management solution", "Estimated cost: ₹5 crore."), settings, catalog)
    assert d.status == REJECTED and d.rejections[0][1] == "OEM_OVER_LIMIT"


# ---------------------------------------------------------------- Test 6: hybrid
def _hybrid_analysis(service_value):
    return llm_analysis(
        [Requirement("Enterprise SIEM platform", "PRODUCT", ["prd_siem"], "SIEM", "DIRECT", 95, "SIEM solution", "SIEM"),
         Requirement("SIEM implementation", "SERVICE", ["mss_infra_mgmt"], "Implementation", "SEMANTIC", 80, "implementation", "impl")],
        [Component("SIEM licences and appliances", "PRODUCT", 40_000_000, "BOQ item 1"),
         Component("Implementation services", "SERVICE", service_value, "BOQ item 2")],
        total=42_000_000)


def test_06_hybrid_siem_4cr_plus_20l_implementation_accept(settings, catalog):
    d = run(ctx("Supply and implementation of SIEM solution", "BOQ: SIEM ₹4 crore; implementation ₹20 lakh"),
            settings, catalog, StubAnalyzer(_hybrid_analysis(2_000_000)))
    assert d.opportunity_type == "HYBRID"
    assert d.commercial.status == "ACCEPT"
    assert d.commercial.service_value_inr == 2_000_000
    assert d.status == ACCEPTED, "total > ₹30 lakh must not reject a hybrid"


def test_06b_hybrid_without_service_split_goes_to_review(settings, catalog):
    d = run(ctx("Supply and implementation of SIEM solution", "Total estimated cost ₹4.2 crore"),
            settings, catalog, StubAnalyzer(_hybrid_analysis(None)))
    assert d.opportunity_type == "HYBRID"
    assert d.status == MANUAL_REVIEW
    assert "HYBRID_REVIEW_REQUIRED" in d.flags


def test_06c_hybrid_review_disabled_accepts(settings, catalog):
    settings.HYBRID_REVIEW_ENABLED = False
    d = run(ctx("Supply and implementation of SIEM solution", "Total estimated cost ₹4.2 crore"),
            settings, catalog, StubAnalyzer(_hybrid_analysis(None)))
    assert d.status == ACCEPTED


def test_06d_hybrid_service_component_over_cap_is_configurable(settings, catalog):
    d = run(ctx("Supply of SIEM with managed SOC services", "x"), settings, catalog, StubAnalyzer(_hybrid_analysis(6_000_000)))
    assert d.status == MANUAL_REVIEW and "SERVICE_COMPONENT_OVER_CAP" in d.flags
    settings.HYBRID_SERVICE_OVER_CAP_ACTION = "REJECT"
    d = run(ctx("Supply of SIEM with managed SOC services", "x"), settings, catalog, StubAnalyzer(_hybrid_analysis(6_000_000)))
    assert d.status == REJECTED


# ---------------------------------------------------------------- Test 7: unrelated
def test_07_civil_construction_4_crore_rejected_without_llm(settings, catalog):
    analyzer = StubAnalyzer(llm_analysis([], []))
    d = run(ctx("Construction of administrative building and associated civil works",
                "Estimated cost: Rs. 4 Crore. Civil works including RCC frame structure.", value=40_000_000),
            settings, catalog, analyzer)
    assert d.opportunity_type == "UNRELATED"
    assert d.status == REJECTED
    assert analyzer.calls == 0


def test_07b_physical_security_guards_not_cyber(settings, catalog):
    d = run(ctx("Providing security services through security guards at regional office"), settings, catalog)
    assert d.status == REJECTED and d.opportunity_type == "UNRELATED"


# ---------------------------------------------------------------- Test 8-12: functional matching -> portfolio
@pytest.mark.parametrize("title, capability, expected_products", [
    ("Tender requiring Security Information and Event Management", "prd_siem", {"splunk", "qradar", "arcsight", "cortex_xsiam"}),
    ("Tender requiring Privileged Access Management", "prd_pam", {"delinea", "arcos"}),
    ("Tender requiring Cloud Security Posture Management", "prd_cspm", {"prisma_cloud", "orca", "tenable_cs"}),
    ("Tender requiring Data Loss Prevention", "prd_dlp", {"digital_guardian", "forcepoint_dlp"}),
    ("Tender requiring Breach and Attack Simulation", "prd_bas", {"attackiq", "xm_cyber"}),
], ids=["08_siem", "09_pam", "10_cspm", "11_dlp", "12_bas"])
def test_08_to_12_functional_requirement_matches_portfolio(catalog, title, capability, expected_products):
    hits = catalog.lexicon_matches(title)
    matches = build_matches(catalog, hits, catalog.oem_mentions(title), None)
    m = next(m for m in matches if m.capability_id == capability)
    assert m.match_type == "DIRECT"
    assert expected_products <= set(m.product_ids)
    assert not m.explicit_oem, "brand was not named — this is a functional match"


@pytest.mark.parametrize("phrase, capability", [
    ("Enterprise SIEM solution with centralised security event correlation", "prd_siem"),
    ("Privileged account management and session recording", "prd_pam"),
    ("Web application penetration testing of citizen portals", "svc_appsec"),
    ("Adversary simulation exercise for the bank", "svc_redteam"),
    ("Security validation platform for continuous control testing", "prd_bas"),
    ("Endpoint detection and response for 2000 endpoints", "prd_xdr"),
    ("Industrial control system security for SCADA network", "prd_ot_security"),
    ("Secure SD-WAN for branch connectivity", "prd_sase"),
])
def test_semantic_matching_examples_from_spec(catalog, phrase, capability):
    caps = {h.capability_id for h in catalog.lexicon_matches(phrase) if h.match_type in ("DIRECT", "SEMANTIC")}
    assert capability in caps

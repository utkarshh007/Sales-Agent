"""Phase 5 — eligibility vs company profile, hybrid analysis, value estimation, scoring v2."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.analysis import Component
from app.catalog import get_catalog
from app.engine import ACCEPTED, MANUAL_REVIEW, REJECTED, evaluate
from app.llm.analyzer import RulesAnalyzer
from app.rules.commercial import apply_commercial_rules
from app.rules.eligibility import assess, extract_criteria
from app.rules.hybrid import boq_components, emd_value_band
from app.semantic import set_semantic_matcher
from tests.conftest import NOW, StubAnalyzer, ctx, llm_analysis
from tests.helpers import FIXTURES

CSCOE = (FIXTURES / "eligibility_sbi_cscoe.txt").read_text(encoding="utf-8")
TTX = (FIXTURES / "eligibility_sbi_ttx.txt").read_text(encoding="utf-8")

PROFILE = {
    "average_turnover_inr": 120_000_000, "profitable_years_last_3": 3, "years_in_business": 9,
    "similar_projects_count": 12, "certifications": ["ISO 27001", "ISO 9001", "CMMI L3"], "empanelments": ["CERT-In"],
    "local_supplier_class": "Class-I", "indian_registered_entity": True, "currently_debarred": False,
    "dpiit_startup": False, "msme": False, "oem_authorisations": ["splunk", "delinea", "arcos"],
}


@pytest.fixture(autouse=True)
def lexicon_only(settings):
    """These tests are about scoring rules; keep matching deterministic and model-free."""
    settings.SEMANTIC_MATCHING_ENABLED = False
    set_semantic_matcher(None)


# ------------------------------------------------------------------ eligibility extraction on real RFPs
def _codes(criteria):
    return {c.code: c.required for c in criteria}


def test_extracts_sbi_ttx_eligibility():
    criteria, startup, msme = extract_criteria(TTX)
    got = _codes(criteria)
    assert got["TURNOVER"] == 100_000_000
    assert got["PROFITABLE_YEARS"] == [2, 3]
    assert got["EXPERIENCE_YEARS"] == 2
    assert got["CLIENT_REFERENCES"] == 3
    assert any(c.code == "CERTIFICATION" and c.required == "ISO 27001" for c in criteria)
    assert got["LOCAL_SUPPLIER"] == "Class-I or Class-II"
    assert {"INDIAN_ENTITY", "NOT_BLACKLISTED"} <= set(got)
    assert startup and not msme


def test_extracts_sbi_cscoe_eligibility():
    criteria, startup, _ = extract_criteria(CSCOE)
    got = _codes(criteria)
    assert got["TURNOVER"] == 10_000_000 and got["EXPERIENCE_YEARS"] == 2
    assert "CERTIFICATION" not in got, "the CSCoE RFP names no specific certificate"
    assert startup


# ------------------------------------------------------------------ assessment against the profile
def test_profile_meeting_all_criteria_is_feasible():
    criteria, s, m = extract_criteria(TTX)
    r = assess(criteria, PROFILE, startup_relaxation=s, msme_relaxation=m)
    assert r.assessment == "FEASIBLE" and not r.gaps and not r.unknowns


def test_turnover_gap_makes_bid_infeasible():
    criteria, s, m = extract_criteria(TTX)
    r = assess(criteria, {**PROFILE, "average_turnover_inr": 60_000_000}, startup_relaxation=s, msme_relaxation=m)
    assert r.assessment == "INFEASIBLE"
    assert [c.code for c in r.gaps] == ["TURNOVER"] and r.gaps[0].company == "₹6 Cr"


def test_startup_relaxation_waives_financial_criteria():
    criteria, s, m = extract_criteria(TTX)
    r = assess(criteria, {**PROFILE, "average_turnover_inr": 5_000_000, "dpiit_startup": True},
               startup_relaxation=s, msme_relaxation=m)
    turnover = next(c for c in r.criteria if c.code == "TURNOVER")
    assert turnover.status == "RELAXED" and r.assessment == "FEASIBLE"


def test_empty_profile_is_unknown_never_assumed_met():
    criteria, s, m = extract_criteria(TTX)
    r = assess(criteria, {}, startup_relaxation=s, msme_relaxation=m)
    assert r.assessment == "UNKNOWN" and all(c.status in ("UNKNOWN", "INFO") for c in r.criteria)


def test_cmmi_higher_level_satisfies_lower_requirement():
    criteria, _, _ = extract_criteria("The bidder must be CMMI Level 3 certified.")
    assert assess(criteria, {"certifications": ["CMMI L5"]}).assessment == "FEASIBLE"
    assert assess(criteria, {"certifications": ["CMMI L2"]}).assessment == "INFEASIBLE"


def test_oem_authorisation_met_by_any_candidate_product():
    criteria, _, _ = extract_criteria("Manufacturer's Authorisation Form (MAF) from the OEM must be submitted.")
    assert assess(criteria, PROFILE, matched_products=["qradar", "splunk"]).assessment == "FEASIBLE"
    r = assess(criteria, PROFILE, matched_products=["qradar", "arcsight"])
    assert r.assessment == "INFEASIBLE" and "qradar" in r.gaps[0].note


# ------------------------------------------------------------------ value estimation and hybrid rules
def test_emd_band_follows_gfr_two_to_five_percent():
    band = emd_value_band(1_000_000)
    assert (band.low, band.high) == (20_000_000, 50_000_000) and "GFR" in band.basis
    assert emd_value_band(0) is None and emd_value_band(None) is None


@pytest.mark.parametrize("emd, code, status", [
    (500_000, "SERVICE_LIKELY_OVER_CAP", "REVIEW"),     # ₹1–2.5 Cr: entirely above ₹30 L
    (40_000, "SERVICE_LIKELY_WITHIN_CAP", "ACCEPT"),    # ₹8–20 L: entirely within
    (100_000, "SERVICE_VALUE_UNKNOWN", "REVIEW"),       # ₹20–50 L: straddles the cap
])
def test_service_without_value_uses_emd_band(settings, emd, code, status):
    d = apply_commercial_rules("SERVICE", settings, total_value_inr=None, value_band=emd_value_band(emd))
    assert (d.code, d.status) == (code, status)


def test_emd_over_cap_action_is_configurable(settings):
    settings.SERVICE_EMD_OVER_CAP_ACTION = "REJECT"
    assert apply_commercial_rules("SERVICE", settings, total_value_inr=None, value_band=emd_value_band(500_000)).status == "REJECT"


def test_stated_value_always_beats_the_estimate(settings):
    d = apply_commercial_rules("SERVICE", settings, total_value_inr=2_000_000, value_band=emd_value_band(500_000))
    assert d.code == "SERVICE_WITHIN_CAP"


def test_hybrid_total_within_cap_needs_no_split(settings):
    d = apply_commercial_rules("HYBRID", settings, total_value_inr=2_500_000)
    assert d.status == "ACCEPT" and d.code == "HYBRID_TOTAL_WITHIN_CAP"


BOQ = """[sheet Price Bid]
S.No | Item Description | Qty | Unit Rate | Total Amount
1 | SIEM software licences perpetual with 3 years ATS | 1 | 30000000 | 30000000
2 | Implementation and integration services | 1 | 1500000 | 1500000
3 | Onsite resident engineer (FMS) 24 man-months | 24 | 100000 | 2400000
 | Grand Total | | | 33900000"""


def test_priced_boq_gives_deterministic_hybrid_split(settings, catalog):
    comps = boq_components(BOQ, catalog)
    assert [(c.kind, c.value_inr) for c in comps] == [("PRODUCT", 30_000_000), ("SERVICE", 1_500_000), ("SERVICE", 2_400_000)]
    d = evaluate(ctx("Supply and implementation of SIEM solution", BOQ), catalog, settings, RulesAnalyzer(settings, catalog), now=NOW)
    assert d.opportunity_type == "HYBRID" and d.value_analysis["service_value_inr"] == 3_900_000
    assert d.commercial.code == "HYBRID_SERVICE_COMPONENT_OVER_CAP" and "BOQ_SPLIT" in d.flags


def test_blank_price_bid_formats_are_ignored(catalog):
    blank = BOQ.replace("30000000", "").replace("1500000", "").replace("2400000", "").replace("100000", "")
    assert boq_components(blank, catalog) == []


# ------------------------------------------------------------------ segments, timeline, next actions
def test_buyer_segments(catalog):
    assert catalog.buyer_segment("Reserve Bank of India")[0] == "Financial regulator"
    assert catalog.buyer_segment("Uttar Pradesh Police")[0] == "Defence or law enforcement"
    assert catalog.buyer_segment("Municipal Corporation of Greater Mumbai")[0] == "State or local government"
    assert catalog.buyer_segment("Some Trust")[0] == "Other"


def test_timeline_depends_on_opportunity_type(settings, catalog):
    analyzer = RulesAnalyzer(settings, catalog)
    service = evaluate(ctx("Red Team Assessment of IT infrastructure", days_to_close=10), catalog, settings, analyzer, now=NOW)
    hybrid = evaluate(ctx("Supply of SIEM solution with 3 years FMS for Security Operations Centre", days_to_close=10),
                      catalog, settings, analyzer, now=NOW)
    assert service.score.breakdown["timeline"]["points"] == 5.0  # 10 days >= 7 needed for a service bid
    assert hybrid.score.breakdown["timeline"]["points"] < 5.0  # a hybrid bid needs ~21 days


def test_next_actions_explain_how_to_raise_the_score(settings, catalog):
    c = ctx("Supply of CrowdStrike Falcon licences for 2000 endpoints", org="Reserve Bank of India")
    c.eligibility_text = TTX
    c.company_profile = {"average_turnover_inr": 50_000_000}
    d = evaluate(c, catalog, settings, RulesAnalyzer(settings, catalog), now=NOW)
    actions = [a["action"] for a in d.score.next_actions]
    assert any(a.startswith("Get the tender documents") for a in actions)
    assert any(a.startswith("Complete the company profile") for a in actions)
    assert any("CrowdStrike" in a and "equivalent" in a for a in actions)
    assert d.score.breakdown["strategic"]["reason"].startswith("Buyer segment: Financial regulator")


def test_eligibility_gap_flags_tender_and_zeroes_eligibility(settings, catalog):
    c = ctx("Procurement of tool for conducting tabletop exercises", org="State Bank of India")
    c.eligibility_text, c.company_profile = TTX, {**PROFILE, "average_turnover_inr": 20_000_000}
    d = evaluate(c, catalog, settings, RulesAnalyzer(settings, catalog), now=NOW)
    assert d.eligibility.assessment == "INFEASIBLE" and "ELIGIBILITY_GAP" in d.flags
    assert d.score.breakdown["eligibility"]["points"] == 0.0
    assert "requires at least ₹10 Cr" in d.score.breakdown["eligibility"]["reason"]


# ------------------------------------------------------------------ end to end + API
def test_uploaded_rfp_eligibility_is_checked_against_saved_profile(tmp_path, settings, monkeypatch):
    from app.models import CompanyProfile, Portal, Tender
    from app.pipeline import store_upload
    from app.worker import Worker
    from tests.helpers import fixture_connector, make_db

    settings.DOCUMENT_STORAGE_DIR = str(tmp_path / "docs")
    Session = make_db(tmp_path)
    with Session() as s:
        s.add(CompanyProfile(id=1, data={**PROFILE, "average_turnover_inr": 40_000_000}))
        s.commit()
        from app.pipeline import discover_portal
        connector, _ = fixture_connector(settings)
        discover_portal(s, s.scalar(select(Portal).where(Portal.code == "cppp")), settings, connector)
    Worker(settings).drain()
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_BANK_2002"))
        doc = "Scope of Work\nRed team assessment of the bank.\nEstimated Cost: Rs. 18,00,000\n\nEligibility Criteria\n" + TTX
        store_upload(s, t, "RFP.txt", doc.encode(), settings, "analyst@x.io")
        s.commit()
        tid = t.id
    Worker(settings).drain()
    with Session() as s:
        t = s.get(Tender, tid)
        check = t.extracted["_eligibility_check"]
        assert check["assessment"] == "INFEASIBLE"
        assert [c["code"] for c in check["criteria"] if c["status"] == "NOT_MET"] == ["TURNOVER"]
        assert "ELIGIBILITY_GAP" in t.flags and t.extracted["_segment"]["name"] == "Bank or financial institution"
        assert any("Eligibility gap" in a["action"] for a in t.extracted["_next_actions"])


def test_company_profile_api(tmp_path, settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.models import User
    from app.security import hash_password, limiter
    from tests.helpers import make_db

    Session = make_db(tmp_path)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
    with Session() as s:
        s.add(User(email="admin@x.io", password_hash=hash_password("Correct-Horse-9-Battery"), role="admin"))
        s.add(User(email="viewer@x.io", password_hash=hash_password("Correct-Horse-9-Battery"), role="viewer"))
        s.commit()
    from app.api.main import create_app
    limiter._hits.clear()
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"email": "viewer@x.io", "password": "Correct-Horse-9-Battery"})
    csrf = c.cookies.get("ti_csrf")
    assert c.get("/api/company-profile").json()["data"] == {}
    assert c.put("/api/company-profile", json=PROFILE, headers={"X-CSRF-Token": csrf}).status_code == 403
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "admin@x.io", "password": "Correct-Horse-9-Battery"})
    csrf = c.cookies.get("ti_csrf")
    r = c.put("/api/company-profile", json=PROFILE, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and "average_turnover_inr" in r.json()["changed"]
    assert c.get("/api/company-profile").json()["data"]["empanelments"] == ["CERT-In"]
    bad = c.put("/api/company-profile", json={"oem_authorisations": ["not_a_product"]}, headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 400


def test_definitions_are_not_requirements():
    text = ("The Bidder should either be Class-I or Class-II local supplier as defined under this RFP. "
            "“Class-I local supplier” means a supplier whose product meets the minimum local content.")
    criteria, _, _ = extract_criteria(text)
    assert [c.required for c in criteria if c.code == "LOCAL_SUPPLIER"] == ["Class-I or Class-II"]

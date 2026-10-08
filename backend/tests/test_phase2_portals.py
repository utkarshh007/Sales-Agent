"""Phase 2 — multiple portals: GePNIC connector, detail enrichment, update detection, cross-portal merge."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.catalog import get_catalog
from app.connectors.gepnic import apply_detail, parse_org_index, parse_org_tenders, parse_tender_detail
from app.connectors.registry import DEFAULT_PORTALS, GEPNIC_PORTALS
from app.models import Portal, ProcessingJob, Tender, TenderSource
from app.pipeline import discover_portal
from app.rules.prefilter import title_is_candidate
from app.worker import Worker
from tests.gepnic_helpers import fake_gepnic_connector, tender
from tests.helpers import FIXTURES, fixture_connector, make_db


# ------------------------------------------------------------------ parsers on live captures
def test_parses_live_gepnic_organisation_index():
    orgs = parse_org_index((FIXTURES / "gepnic_org_index.html").read_text(encoding="utf-8"))
    assert len(orgs) == 83
    assert orgs[0]["name"] == "AAI Cargo Logistics and Allied Services Company Ltd" and orgs[0]["count"] == 3
    assert orgs[0]["href"].startswith("/eprocure/app?component=%24DirectLink&page=FrontEndTendersByOrganisation")


def test_parses_live_gepnic_organisation_tenders_and_detail():
    items = parse_org_tenders((FIXTURES / "gepnic_org_tenders.html").read_text(encoding="utf-8"))
    assert len(items) == 15
    t = items[0]
    assert t.title == "Term Contract for miscellaneous electrical work inside factory Premises"
    assert t.reference_number == "RFI/TE/RD2612/2026-27/EO (C)" and t.portal_tender_id == "2026_AWEIL_293528_1"
    assert t.organization == "ADVANCED WEAPONS AND EQUIPMENT INDIA LTD-AWEIL"
    assert t.department == "RIFLE FACTORY ISHAPORE - KOLKATA"
    assert t.closing_at.isoformat() == "2026-10-24T18:00:00+05:30"

    apply_detail(t, parse_tender_detail((FIXTURES / "gepnic_tender_detail.html").read_text(encoding="utf-8")))
    assert t.tender_value_inr == 450_000 and t.emd_inr == 9_000
    assert t.category == "Civil Works" and t.tender_type == "Open Tender / Works"
    assert t.contact_info.startswith("Navin Kumar Roy")
    assert "Work description: Term Contract" in t.portal_text
    assert t.raw["document_names"] == "Tendernotice_1.pdf; TenderDocument.pdf; NIT.pdf; BOQ_342212.xls"
    assert t.detail_fetched and "CAPTCHA" in t.documents_blocker


def test_document_download_page_is_recognised_as_captcha():
    from app.connectors.gepnic import CAPTCHA_PAGE
    assert CAPTCHA_PAGE.search((FIXTURES / "gepnic_doc_captcha.html").read_text(encoding="utf-8"))


def test_registry_has_verified_gepnic_portals_with_central_ones_enabled():
    codes = {p["code"]: p for p in DEFAULT_PORTALS}
    assert len(GEPNIC_PORTALS) == 31
    assert codes["gepnic_central"]["enabled"] and codes["gepnic_cpse"]["enabled"] and codes["gepnic_defence"]["enabled"]
    assert not codes["gepnic_maharashtra"]["enabled"], "state portals start paused; admins enable the states they bid in"
    assert all(p["config"]["base_url"].endswith(("/eprocure/app", "/nicgep/app")) for c, p in codes.items() if c.startswith("gepnic_"))


def test_title_candidate_screen():
    cat = get_catalog()
    assert title_is_candidate("Supply of SIEM solution", None, cat)
    assert title_is_candidate("Hiring of agency for cyber security audit", None, cat)
    assert not title_is_candidate("Construction of boundary wall", None, cat)
    assert not title_is_candidate("Supply of CCTV cameras", None, cat)


# ------------------------------------------------------------------ connector + pipeline
ORGS = {
    "State IT Department": [
        tender("2026_SITD_101_1", "Comprehensive cyber security audit and VAPT of state data centre", "SITD/CS/01",
               value="25,00,000", category="Services"),
        tender("2026_SITD_102_1", "Supply of Privileged Access Management solution", "SITD/PAM/02",
               value="3,20,00,000", category="Computer Software"),
        tender("2026_SITD_103_1", "Engagement of red team for annual adversary simulation", "SITD/RT/03",
               value="45,00,000", category="Services"),
    ],
    "Public Works Department": [
        tender("2026_PWD_201_1", "Construction of district office building", "PWD/EE/9", value="4,10,00,000",
               category="Civil Works"),
        tender("2026_PWD_202_1", "Annual maintenance of network and security systems", "PWD/EE/10", value="8,00,000",
               category="Civil Works", description="Maintenance of boom barriers and access control gates"),
    ],
}


@pytest.fixture
def env(tmp_path, settings):
    settings.DOCUMENT_STORAGE_DIR = str(tmp_path / "docs")
    Session = make_db(tmp_path)
    with Session() as s:
        s.add(Portal(code="gepnic_test", name="Test State eTenders", connector="gepnic_html", base_url="x",
                     acquisition_method="HTML", config={}))
        s.commit()
    return Session, settings


def _discover(Session, settings, connector, code="gepnic_test"):
    with Session() as s:
        return discover_portal(s, s.scalar(select(Portal).where(Portal.code == code)), settings, connector)


def test_gepnic_end_to_end_with_detail_only_for_candidates(env):
    Session, settings = env
    connector, fake = fake_gepnic_connector(settings, ORGS)
    summary = _discover(Session, settings, connector)
    assert summary["NEW"] == 5
    # only titles with a catalog signal get a detail request; "network and security systems" maintenance
    # and building construction are screened out at listing level
    assert set(fake.detail_requests) == {"2026_SITD_101_1", "2026_SITD_102_1", "2026_SITD_103_1"}
    Worker(settings).drain()
    with Session() as s:
        t = {x.portal_tender_id: x for x in s.scalars(select(Tender))}
        audit_ = t["2026_SITD_101_1"]
        assert audit_.tender_value_inr == 2_500_000 and audit_.emd_inr == 50_000
        assert audit_.opportunity_type == "SERVICE" and audit_.decision == "ACCEPTED", audit_.rejection_reason
        assert "within the ₹30 Lakh" in audit_.relevance_reason

        pam = t["2026_SITD_102_1"]
        assert pam.opportunity_type == "OEM" and pam.decision == "ACCEPTED" and pam.tender_value_inr == 32_000_000

        red = t["2026_SITD_103_1"]
        assert red.decision == "REJECTED" and "exceeds the ₹30 Lakh" in red.rejection_reason, \
            "portal-stated value lets the service cap fire before any document is uploaded"

        assert t["2026_PWD_201_1"].decision == "REJECTED" and t["2026_PWD_201_1"].tender_value_inr is None
        assert t["2026_PWD_202_1"].decision == "REJECTED"
        assert audit_.sources[0].lookup_hint.startswith("On state.example.gov.in, open “Tenders by Organisation”")
        assert "Work description" in audit_.raw["portal_text"]


def test_rerun_without_changes_fetches_no_details_and_queues_nothing(env):
    Session, settings = env
    connector, _ = fake_gepnic_connector(settings, ORGS)
    _discover(Session, settings, connector)
    Worker(settings).drain()
    with Session() as s:
        jobs_before = s.scalar(select(func.count(ProcessingJob.id)))
    connector, fake = fake_gepnic_connector(settings, ORGS)
    summary = _discover(Session, settings, connector)
    assert summary["UNCHANGED"] == 5 and summary["NEW"] == 0 and summary["UPDATED"] == 0
    assert fake.detail_requests == [], "already-detailed, unchanged tenders are not re-fetched"
    with Session() as s:
        assert s.scalar(select(func.count(ProcessingJob.id))) == jobs_before


def test_closing_date_extension_refetches_detail_and_reanalyses(env):
    Session, settings = env
    connector, _ = fake_gepnic_connector(settings, ORGS)
    _discover(Session, settings, connector)
    changed = {k: [dict(t) for t in v] for k, v in ORGS.items()}
    changed["State IT Department"][1] = tender("2026_SITD_102_1", "Supply of Privileged Access Management solution",
                                               "SITD/PAM/02", value="3,60,00,000", category="Computer Software",
                                               close_days=35)
    connector, fake = fake_gepnic_connector(settings, changed)
    summary = _discover(Session, settings, connector)
    assert summary["UPDATED"] == 1 and fake.detail_requests == ["2026_SITD_102_1"]
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_SITD_102_1"))
        assert t.version == 2 and t.tender_value_inr == 36_000_000


def test_detail_cap_defers_remaining_candidates_to_next_run(env):
    Session, settings = env
    connector, fake = fake_gepnic_connector(settings, ORGS, max_details_per_run=2)
    summary = _discover(Session, settings, connector)
    assert len(fake.detail_requests) == 2 and "detail cap" in summary["stopped"]
    connector, fake = fake_gepnic_connector(settings, ORGS, max_details_per_run=10)
    _discover(Session, settings, connector)
    assert fake.detail_requests == ["2026_SITD_103_1"], "only the candidate that was never detailed is fetched"


def test_same_tender_on_cppp_and_gepnic_is_merged_and_enriched(env):
    Session, settings = env
    # CPPP lists the tender (title/ref/ID only)...
    rows = [dict(pub="01-Oct-2026 10:00 AM", close=t["close"], open=t["open"], tid=t["tid"], ref=t["ref"],
                 org="State IT Department", title=t["title"]) for t in ORGS["State IT Department"][:1]]
    cppp, _ = fixture_connector(settings, rows)
    _discover(Session, settings, cppp, code="cppp")
    Worker(settings).drain()
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_SITD_101_1"))
        assert t.decision == "MANUAL_REVIEW" and t.tender_value_inr is None  # service, value unknown
    # ...and the GePNIC instance hosting it supplies the details
    connector, _ = fake_gepnic_connector(settings, {"State IT Department": ORGS["State IT Department"][:1]},
                                         code="gepnic_central")
    summary = _discover(Session, settings, connector, code="gepnic_central")
    assert summary["MERGED"] == 1 and summary["NEW"] == 0
    Worker(settings).drain()
    with Session() as s:
        assert s.scalar(select(func.count(Tender.id)).where(Tender.portal_tender_id == "2026_SITD_101_1")) == 1
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_SITD_101_1"))
        assert [src.match_basis for src in t.sources] == ["PRIMARY", "TENDER_ID+REF"]
        assert t.tender_value_inr == 2_500_000
        assert t.decision == "ACCEPTED", "the merged portal value resolves the manual review"


def test_tender_id_collision_with_different_reference_is_not_merged(env):
    Session, settings = env
    with Session() as s:
        s.add(Portal(code="gepnic_other", name="Other state", connector="gepnic_html", base_url="x",
                     acquisition_method="HTML", config={}))
        s.commit()
    connector, _ = fake_gepnic_connector(settings, {"A": [tender("2026_PWD_1_1", "Supply of SIEM solution", "A/1")]})
    _discover(Session, settings, connector)
    connector, _ = fake_gepnic_connector(settings, {"B": [tender("2026_PWD_1_1", "Repair of roads in block B", "B/77")]},
                                         code="gepnic_other")
    summary = _discover(Session, settings, connector, code="gepnic_other")
    assert summary["NEW"] == 1 and summary["MERGED"] == 0
    with Session() as s:
        assert s.scalar(select(func.count(TenderSource.id))) == 2


def test_listing_blocked_by_captcha_is_logged_and_run_stops(env, settings):
    import httpx

    from app.connectors.base import PoliteHttpClient
    from app.connectors.gepnic import GepnicConnector
    from tests.helpers import FIXTURES as F

    captcha = (F / "gepnic_doc_captcha.html").read_text(encoding="utf-8")
    http = PoliteHttpClient(settings, delay_seconds=0)
    http.client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, text=captcha, headers={"content-type": "text/html"})))
    res = GepnicConnector("x", {"base_url": "https://s.example/nicgep/app"}, settings, http).discover(lambda fp: False)
    assert res.blockers and res.items == []


# ------------------------------------------------------------------ precision regressions from live GePNIC data
LIVE_FALSE_FRIENDS = [
    # "DLP" = defect liability period
    "Supply, Installation, Testing and Commissioning of Packaged, ducted and Split AC type Air Conditioners along with "
    "all accessories with 2 year DLP and 10 years Comprehensive Annual Maintenance Contract at stations and Depot of "
    "Airport Express Line.",
    "Design, Supply, Installation, Testing, Commissioning and Integrated test of Facade lighting work along-with 2 years "
    "DLP and further 3 years AMC beyond 2 years DLP at Sarai Kale Khan Namo Bharat Station",
    # "MDR" = major district road
    "Construction of 4lane GF Amritsar-Katra connectivity of Amritsar with DAK Expressway from MDR Junction at ch.40.900 "
    "Dhunda Village-Junction NH3 and Taran Taran Bypass",
    # CyberKnife = radiosurgery device
    "SITC of Cyber knife",
]


@pytest.mark.parametrize("title", LIVE_FALSE_FRIENDS)
def test_live_false_friend_acronyms_are_not_matches(settings, title):
    from app.engine import evaluate
    from app.llm.analyzer import RulesAnalyzer
    from tests.conftest import ctx

    cat = get_catalog()
    assert not title_is_candidate(title, None, cat)
    d = evaluate(ctx(title, value=50_000_000), cat, settings, RulesAnalyzer(settings, cat))
    assert d.status == "REJECTED" and d.opportunity_type == "UNRELATED", d.primary_reason


def test_cyber_security_infrastructure_supply_goes_to_review(settings):
    from app.engine import evaluate
    from app.llm.analyzer import RulesAnalyzer
    from tests.conftest import ctx

    cat = get_catalog()
    d = evaluate(ctx("EOI for Selection of partner for SITC of Cyber Security Infrastructure for CNS ATM Systems at "
                     "Chennai Airport", org="Airports Authority of India"), cat, settings, RulesAnalyzer(settings, cat))
    assert d.status == "MANUAL_REVIEW" and d.reviews[0][0] == "ADJACENT_ONLY"

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.connectors.base import HumanInterventionRequired, PoliteHttpClient
from app.connectors.cppp import CpppConnector, listing_url, parse_listing_html
from tests.helpers import CYBER_ROWS, FIXTURES, fixture_connector, listing_html


def test_parses_live_cppp_central_listing():
    lp = parse_listing_html((FIXTURES / "cppp_central_page1.html").read_text(encoding="utf-8"), "cpppdata")
    assert len(lp.items) == 10 and lp.has_next
    first = lp.items[0]
    assert first.portal_tender_id == "2026_BPCL_26725"
    assert first.reference_number == "1000465311"
    assert first.organization == "Bharat Petroleum Corporation Limited"
    assert first.title.startswith("CONSTRUCTION OF LUBE WAREHOUSE SHED")
    assert first.closing_at.isoformat() == "2026-10-28T15:00:00+05:30"
    assert first.source_url.startswith("https://eprocure.gov.in/cppp/tendersfullview/")
    assert first.documents_blocker and "CAPTCHA" in first.documents_blocker
    # references containing "/" are kept whole; the tender id is the last segment
    tl = next(i for i in lp.items if i.portal_tender_id == "28756")
    assert tl.reference_number == "CC/T/W-TW/DOM/A06/26/13841"


def test_parses_live_cppp_state_listing_into_location():
    lp = parse_listing_html((FIXTURES / "cppp_mmp_page1.html").read_text(encoding="utf-8"), "mmpdata")
    assert len(lp.items) == 10
    assert lp.items[0].location == "West Bengal" and lp.items[0].organization is None


def test_pagination_url_matches_portal_format():
    url = listing_url("cpppdata", 2)
    target = parse_qs(urlparse(url).query)["url"][0]
    assert base64.b64decode(target).decode() == "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata?page=2"
    assert listing_url("cpppdata", 1).endswith("/cpppdata")


def test_unsupported_listing_rejected(settings):
    with pytest.raises(ValueError):
        CpppConnector("cppp", {"listings": ["gemdata"]}, settings).listings()


def _client(settings, handler):
    http = PoliteHttpClient(settings, delay_seconds=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    return http


def test_captcha_page_raises_human_intervention(settings):
    captcha_html = (FIXTURES / "cppp_detail_captcha.html").read_text(encoding="utf-8")

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=captcha_html, headers={"content-type": "text/html"})

    with pytest.raises(HumanInterventionRequired):
        _client(settings, handler).get("https://eprocure.gov.in/cppp/tendersfullview/abc")


def test_robots_txt_is_respected(settings):
    from app.connectors.base import PortalAccessDenied

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        return httpx.Response(200, text="ok")

    http = _client(settings, handler)
    with pytest.raises(PortalAccessDenied):
        http.get("https://portal.example/private/tenders")
    assert http.get("https://portal.example/public/tenders").text == "ok"


def test_discovery_stops_when_pages_are_already_known(settings):
    connector, _ = fixture_connector(settings)
    known = {connector.fingerprint(i) for i in parse_listing_html(listing_html(CYBER_ROWS)).items}
    res = connector.discover(lambda fp: fp in known)
    assert res.pages_fetched == 1 and "consecutive pages" in res.stopped_reason


def test_discovery_logs_blocker_and_continues(settings):
    class Blocked(CpppConnector):
        def list_page(self, listing, page):
            raise HumanInterventionRequired("CAPTCHA on listing")

    res = Blocked("cppp", {"listings": ["cpppdata", "mmpdata"]}, settings).discover(lambda fp: False)
    assert len(res.blockers) == 2 and res.items == []

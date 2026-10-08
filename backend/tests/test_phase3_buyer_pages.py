"""Phase 3 — buyer tender pages, browser rendering, direct document download, access safety."""
from __future__ import annotations

import http.server
import io
import threading
from datetime import datetime, timedelta

import httpx
import pymupdf
import pytest
from sqlalchemy import select

from app.catalog import get_catalog
from app.connectors.base import PoliteHttpClient, PortalAccessDenied, ensure_public_url, is_public_host
from app.connectors.buyer_page import (
    BuyerPageConnector, extract_document_links, extract_tenders, find_dates, map_columns,
)
from app.connectors.registry import DEFAULT_PORTALS
from app.models import Portal, Tender
from app.pipeline import discover_portal
from app.worker import Worker
from tests.helpers import FIXTURES, make_db

SBI_URL = "https://sbi.bank.in/web/sbi-in-the-news/procurement-news"
SBI_CFG = {"reference_regex": r"^(?P<ref>[A-Z0-9][^:]{3,80}):"}


# ------------------------------------------------------------------ parsing on live captures
@pytest.mark.parametrize("text, expected", [
    ("12-Oct-2026", ["2026-10-12 00:00"]),
    ("2026-10-16 16:00:00", ["2026-10-16 16:00"]),
    ("Monday, September 28, 2026 - to Tuesday, November 10, 2026 - 14:00", ["2026-09-28 00:00", "2026-11-10 14:00"]),
    ("Dated:28.09.2026", ["2026-09-28 00:00"]),
    ("24/09/2026", ["2026-09-24 00:00"]),
    ("07-Oct-2026 06:00 PM", ["2026-10-07 18:00"]),
    ("Sept 5, 2026", ["2026-09-05 00:00"]),
])
def test_find_dates_handles_indian_portal_formats(text, expected):
    assert [d.strftime("%Y-%m-%d %H:%M") for d in find_dates(text)] == expected


def test_column_mapping_from_headers():
    cols = map_columns(["Location", "Tender Description", "Start Date", "End Date", ""])
    assert cols == {"location": 0, "title": 1, "published": 2, "closing": 3}


def test_extracts_sbi_procurement_table():
    items = extract_tenders((FIXTURES / "buyer_sbi.html").read_text(encoding="utf-8"), SBI_URL, SBI_CFG, "State Bank of India")
    assert len(items) >= 40
    first = items[0]
    assert first.reference_number == "AO/DHN/RBO-4/2026-27/01/001"
    assert first.title.startswith("TENDER NOTICE FOR REQUIREMENT FOR COMMERC"), "reference is stripped from the title"
    assert first.location == "LHO, PATNA" and first.organization == "State Bank of India"
    assert first.closing_at.strftime("%Y-%m-%d %H:%M") == "2026-10-26 23:59", "bare closing date = end of day"
    assert first.raw["doc_links"][0]["url"].startswith("https://sbi.bank.in/documents/")
    cyber = [i for i in items if "CYBERSECURITY TRAINING" in i.title]
    assert cyber and cyber[0].reference_number.startswith("SBI/GITC/INFORMATION SECURITY DEPARTMENT")


def test_extracts_isro_date_ranges_and_advertiser():
    items = extract_tenders((FIXTURES / "buyer_isro.html").read_text(encoding="utf-8"),
                            "https://www.isro.gov.in/Tenders.html", {}, "ISRO")
    vssc = next(i for i in items if "VSSC/P/ADVT/MME/35/2026" in i.title)
    assert vssc.published_at.strftime("%Y-%m-%d") == "2026-09-28"
    assert vssc.closing_at.strftime("%Y-%m-%d %H:%M") == "2026-11-10 14:00"
    assert vssc.department.startswith("Vikram Sarabhai Space Centre")
    assert vssc.raw["doc_links"][0]["url"].endswith(".pdf")


def test_extracts_cdac_tables_and_detail_documents_without_navigation_links():
    items = extract_tenders((FIXTURES / "buyer_cdac.html").read_text(encoding="utf-8"),
                            "https://www.cdac.in/index.aspx?id=tenders", {}, "C-DAC")
    assert len(items) >= 10 and all(i.raw["detail_url"].startswith("https://www.cdac.in/index.aspx?id=tenders_details") for i in items)
    docs = extract_document_links((FIXTURES / "buyer_cdac_detail.html").read_text(encoding="utf-8"),
                                  "https://www.cdac.in/index.aspx?id=tenders_details&token=x")
    assert [d.url.split("id=")[1][:15] for d in docs] == ["tenders_viewpdf"], "site 'Downloads' menu is not a document"


def test_registry_buyer_pages():
    codes = {p["code"]: p for p in DEFAULT_PORTALS}
    assert codes["buyer_sbi"]["connector"] == "buyer_page" and codes["buyer_cdac"]["config"]["follow_detail"]
    assert codes["buyer_isro"]["enabled"] is False and codes["buyer_isro"]["config"]["screen"] == "documents"


# ------------------------------------------------------------------ access safety
@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/admin", "http://10.0.0.5/", "http://192.168.1.1/",
                                 "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "file:///etc/passwd"])
def test_internal_addresses_are_never_fetched(url):
    with pytest.raises(PortalAccessDenied):
        ensure_public_url(url)


def test_public_addresses_allowed():
    assert is_public_host("8.8.8.8")
    ensure_public_url("https://8.8.8.8/tenders")


def test_redirect_to_internal_address_is_blocked(settings):
    http = PoliteHttpClient(settings, delay_seconds=0)
    hook = http.client.event_hooks["request"][0]
    with pytest.raises(PortalAccessDenied):
        hook(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/"))


# ------------------------------------------------------------------ connector + full document pipeline
def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.split("\n"):
        page.insert_text((72, y), line)
        y += 16
    out = doc.tobytes()
    doc.close()
    return out


def _date(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%d-%b-%Y")


PAGE = """<html><body><nav><a href="/downloads">Downloads</a></nav><table>
<tr><th>Location</th><th>Tender Description</th><th>Start Date</th><th>End Date</th><th></th></tr>
<tr><td>GITC</td><td>BANK/ISD/01: PROCUREMENT OF SIEM SOLUTION WITH LICENCES</td><td>{p}</td><td>{c}</td><td><a href="/docs/rfp_siem.pdf">RFP</a></td></tr>
<tr><td>LHO</td><td>BANK/PREM/77: REQUIREMENT OF OFFICE PREMISES ON LEASE</td><td>{p}</td><td>{c}</td><td><a href="/docs/premises.pdf">NIT</a></td></tr>
<tr><td>LHO</td><td>BANK/OLD/12: SUPPLY OF FIREWALL</td><td>{old}</td><td>{old2}</td><td><a href="/docs/old.pdf">NIT</a></td></tr>
</table></body></html>"""


class FakeBank:
    def __init__(self):
        self.downloads: list[str] = []
        self.page = PAGE.format(p=_date(-2), c=_date(20), old=_date(-60), old2=_date(-30))

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        if path == "/tenders":
            return httpx.Response(200, text=self.page, headers={"content-type": "text/html"})
        if path.startswith("/docs/"):
            self.downloads.append(path)
            body = _pdf("REQUEST FOR PROPOSAL\nScope of Work\nSupply, implementation of Security Information and Event Management\n"
                        "platform (Splunk or equivalent) with licences for 5 years.\nEstimated Cost: Rs. 2 Crore")
            return httpx.Response(200, content=body, headers={"content-type": "application/pdf"})
        return httpx.Response(404)


def bank_connector(settings, **cfg):
    fake = FakeBank()
    http = PoliteHttpClient(settings, delay_seconds=0)
    http.client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    config = {"pages": [{"url": "https://bank.example.in/tenders", "organization": "Example Bank"}],
              "reference_regex": r"^(?P<ref>[A-Z0-9][^:]{3,80}):", **cfg}
    return BuyerPageConnector("buyer_bank", config, settings, http), fake


@pytest.fixture
def env(tmp_path, settings):
    settings.DOCUMENT_STORAGE_DIR = str(tmp_path / "docs")
    Session = make_db(tmp_path)
    with Session() as s:
        s.add(Portal(code="buyer_bank", name="Example Bank", connector="buyer_page", base_url="x",
                     acquisition_method="HTML", config={}))
        s.commit()
    return Session, settings


def _discover(Session, settings, connector, monkeypatch=None):
    if monkeypatch is not None:
        # document downloads run later in the worker, which rebuilds the connector from the portal row;
        # point it at the same fake bank
        monkeypatch.setattr("app.pipeline.build_connector", lambda *a, **k: connector)
    with Session() as s:
        return discover_portal(s, s.scalar(select(Portal).where(Portal.code == "buyer_bank")), settings, connector)


def test_buyer_page_downloads_documents_only_for_candidates_and_qualifies_them(env, monkeypatch):
    Session, settings = env
    connector, fake = bank_connector(settings)
    summary = _discover(Session, settings, connector, monkeypatch)
    assert summary["NEW"] == 2, "the closed tender is skipped"
    Worker(settings).drain()
    assert fake.downloads == ["/docs/rfp_siem.pdf"], "only the SIEM candidate's RFP is downloaded"
    with Session() as s:
        siem = s.scalar(select(Tender).where(Tender.reference_number == "BANK/ISD/01"))
        assert siem.title == "PROCUREMENT OF SIEM SOLUTION WITH LICENCES"
        assert siem.documents[0].status == "EXTRACTED" and "Security Information" in siem.documents[0].extracted_text
        assert siem.value_analysis["total_value_inr"] == 20_000_000
        assert siem.opportunity_type == "OEM" and siem.decision == "ACCEPTED"
        premises = s.scalar(select(Tender).where(Tender.reference_number == "BANK/PREM/77"))
        assert premises.decision == "REJECTED" and not premises.documents
    # second run: nothing new, nothing downloaded again
    connector, fake = bank_connector(settings)
    summary = _discover(Session, settings, connector, monkeypatch)
    assert summary["UNCHANGED"] == 2 and fake.downloads == []


def test_document_screening_mode_reads_every_new_notice(env, monkeypatch):
    Session, settings = env
    connector, fake = bank_connector(settings, screen="documents")
    _discover(Session, settings, connector, monkeypatch)
    Worker(settings).drain()
    assert sorted(fake.downloads) == ["/docs/premises.pdf", "/docs/rfp_siem.pdf"]


def test_robots_disallowed_page_is_a_blocker(env):
    Session, settings = env
    connector, _ = bank_connector(settings)
    connector.config["pages"] = [{"url": "https://bank.example.in/private/tenders", "organization": "Example Bank"}]
    summary = _discover(Session, settings, connector)
    assert summary["NEW"] == 0 and "robots.txt disallows" in summary["blockers"][0]


# ------------------------------------------------------------------ real headless browser
JS_PAGE = b"""<html><body><div id="list">Loading...</div><script>
document.getElementById('list').innerHTML = '<table><tr><th>Title</th><th>Last Date</th></tr>' +
  '<tr><td><a href="/t/1">Procurement of Privileged Access Management solution</a></td><td>DATE</td></tr></table>';
</script></body></html>"""


@pytest.fixture
def js_site(monkeypatch):
    pytest.importorskip("playwright")
    body = JS_PAGE.replace(b"DATE", _date(15).encode())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # the test server is on loopback, which the SSRF guard (correctly) refuses in production
    monkeypatch.setattr("app.connectors.base.is_public_host", lambda host: True)
    yield f"http://127.0.0.1:{server.server_address[1]}/tenders"
    server.shutdown()


def test_browser_rendering_reads_tables_built_by_javascript(settings, js_site):
    cfg = {"pages": [{"url": js_site, "organization": "JS Agency"}]}
    plain = BuyerPageConnector("js", {**cfg, "render": "http"}, settings).discover(lambda fp: False)
    assert plain.items == [] and "no tender table" in plain.errors[0]
    try:
        rendered = BuyerPageConnector("js", {**cfg, "render": "browser", "wait_for": "table"}, settings).discover(lambda fp: False)
    except RuntimeError as e:  # browser binaries not installed on this machine
        pytest.skip(str(e))
    if rendered.errors and "Executable doesn't exist" in rendered.errors[0]:
        pytest.skip("Chromium not installed")
    assert [i.title for i in rendered.items] == ["Procurement of Privileged Access Management solution"]
    assert rendered.items[0].raw["detail_url"].endswith("/t/1")

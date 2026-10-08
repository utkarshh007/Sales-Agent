"""A fake GePNIC instance (index -> organisation pages -> detail pages) for connector/pipeline tests.
Markup mirrors the live portal (see fixtures/gepnic_*.html)."""
from __future__ import annotations

import html as html_lib
from urllib.parse import parse_qs, urlparse

import httpx

from app.connectors.base import PoliteHttpClient
from app.connectors.gepnic import GepnicConnector
from tests.helpers import portal_date

BASE = "https://state.example.gov.in/nicgep/app"


def tender(tid, title, ref, org="State IT Department", value="25,00,000", emd="50,000", category="Services",
           description=None, close_days=20):
    return dict(tid=tid, title=title, ref=ref, org=org, value=value, emd=emd, category=category,
                description=description or title, pub=portal_date(-3, "10:00 AM"), close=portal_date(close_days),
                open=portal_date(close_days + 1, "11:00 AM"))


def _index(orgs: dict[str, list[dict]]) -> str:
    rows = "".join(
        f'<tr><td>{i + 1}</td><td>{html_lib.escape(name)}</td><td><a href="/nicgep/app?component=%24DirectLink&amp;'
        f'page=FrontEndTendersByOrganisation&amp;service=direct&amp;session=T&amp;sp=ORG{i}">{len(ts)}</a></td></tr>'
        for i, (name, ts) in enumerate(orgs.items()))
    return f"<html><body><table id='table'><tr><td>S.No</td><td>Organisation Name</td><td>Tender Count</td></tr>{rows}</table></body></html>"


def _org_page(org: str, ts: list[dict]) -> str:
    rows = "".join(
        f'<tr class="even"><td>{i + 1}</td><td>{t["pub"]}</td><td>{t["close"]}</td><td>{t["open"]}</td>'
        f'<td><a href="/nicgep/app?component=%24DirectLink&amp;page=FrontEndViewTender&amp;service=direct&amp;session=T&amp;sp=T{t["tid"]}" '
        f'id="DirectLink">[{html_lib.escape(t["title"])}]</a>[{html_lib.escape(t["ref"])}][{t["tid"]}]</td>'
        f'<td>{html_lib.escape(org)}||{html_lib.escape(org)} HQ</td></tr>'
        for i, t in enumerate(ts))
    return f"<html><body><table id='table'><tr><td>S.No</td></tr>{rows}</table></body></html>"


def _detail(t: dict) -> str:
    pairs = [("Organisation Chain", t["org"]), ("Tender Reference Number", t["ref"]), ("Tender ID", t["tid"]),
             ("Tender Type", "Open Tender"), ("Tender Category", t["category"]), ("EMD Amount in ₹", t["emd"]),
             ("Title", t["title"]), ("Work Description", t["description"]), ("NDA/Pre Qualification", "CERT-In empanelment"),
             ("Tender Value in ₹", t["value"]), ("Product Category", t["category"]), ("Sub category", "NA"),
             ("Period Of Work(Days)", "90"), ("Location", "Capital City"), ("Published Date", t["pub"]),
             ("Bid Opening Date", t["open"]), ("Bid Submission End Date", t["close"])]
    cells = "".join(f'<tr><td class="td_caption">{k}</td><td class="td_field">{html_lib.escape(v)}</td></tr>' for k, v in pairs)
    return (f"<html><body><table>{cells}</table><table id='workItemDocumenttable'><tr><td>1</td><td>Tender Documents</td>"
            f"<td><a href='#'>RFP_{t['tid']}.pdf</a></td></tr></table></body></html>")


class FakeGepnic:
    def __init__(self, orgs: dict[str, list[dict]]):
        self.orgs = orgs
        self.detail_requests: list[str] = []
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        q = parse_qs(urlparse(url).query)
        page, sp = q.get("page", [""])[0], q.get("sp", [""])[0]
        ok = lambda body: httpx.Response(200, text=body, headers={"content-type": "text/html"})  # noqa: E731
        if page == "FrontEndTendersByOrganisation" and not sp:
            return ok(_index(self.orgs))
        if page == "FrontEndTendersByOrganisation" and sp.startswith("ORG"):
            name = list(self.orgs)[int(sp[3:])]
            return ok(_org_page(name, self.orgs[name]))
        if page == "FrontEndViewTender":
            tid = sp[1:]
            self.detail_requests.append(tid)
            for org, ts in self.orgs.items():
                for t in ts:
                    if t["tid"] == tid:
                        return ok(_detail({**t, "org": org}))
        return httpx.Response(404)


def fake_gepnic_connector(settings, orgs, code="gepnic_test", **config):
    fake = FakeGepnic(orgs)
    http = PoliteHttpClient(settings, delay_seconds=0)
    http.client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    cfg = {"base_url": BASE, "detail_mode": "candidates", **config}
    return GepnicConnector(code, cfg, settings, http), fake

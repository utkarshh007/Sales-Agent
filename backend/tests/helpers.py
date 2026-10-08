"""Shared helpers for DB-backed tests: an isolated SQLite DB and a CPPP connector served from fixtures."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app.catalog import get_catalog
from app.config import Settings
from app.connectors.base import PoliteHttpClient
from app.connectors.cppp import CpppConnector

FIXTURES = Path(__file__).parent / "fixtures"


def portal_date(days_from_today: int, time: str = "03:00 PM") -> str:
    """CPPP-formatted date relative to today, so the suite does not rot as calendar time passes."""
    return (datetime.now() + timedelta(days=days_from_today)).strftime("%d-%b-%Y ") + time

ROW = """<tbody><tr><td>{n}.</td><td>{pub}</td><td>{close}</td><td>{open}</td>
<td><a href="https://eprocure.gov.in/cppp/tendersfullview/{tid}" title="External Url">{title}</a>/{ref}/{tid}</td>
<td>{org}</td><td>--</td></tr></tbody>"""


def listing_html(rows: list[dict], has_next: bool = False) -> str:
    body = "".join(ROW.format(n=i + 1, **r) for i, r in enumerate(rows))
    nxt = '<a href="#" class="paginate_button">Next »</a>' if has_next else ""
    return f"<html><body>{nxt}<table id='table'><thead><tr><th>x</th></tr></thead>{body}</table></body></html>"


CYBER_ROWS = [
    dict(pub=portal_date(-7, "10:00 AM"), close=portal_date(22), open=portal_date(23, "03:30 PM"), tid="2026_NIC_1001",
         ref="NIC/SIEM/2026/01", org="National Informatics Centre",
         title="Supply, installation and commissioning of Splunk Enterprise Security SIEM solution"),
    dict(pub=portal_date(-7, "10:00 AM"), close=portal_date(17), open=portal_date(18, "03:30 PM"), tid="2026_BANK_2002",
         ref="BANK/IT/RT/07", org="State Bank of India",
         title="Red Team Assessment of the bank's IT infrastructure"),
    dict(pub=portal_date(-7, "10:00 AM"), close=portal_date(12), open=portal_date(13, "03:30 PM"), tid="2026_CPWD_3003",
         ref="CPWD/EE/44", org="Central Public Works Department",
         title="Construction of administrative building and associated civil works"),
    dict(pub=portal_date(-7, "10:00 AM"), close=portal_date(14), open=portal_date(15, "03:30 PM"), tid="2026_PSU_4004",
         ref="PSU/SEC/12", org="Bharat Petroleum Corporation Limited",
         title="Providing security guards for depot (watch and ward)"),
]


class FixtureTransport:
    """Serves page 1 = synthetic cyber rows, page 2 = the real CPPP page captured from the live site."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        if "?url=" in url:
            html = (FIXTURES / "cppp_central_page1.html").read_text(encoding="utf-8")
            return httpx.Response(200, text=html.replace(">Next", ">NoMore"), headers={"content-type": "text/html"})
        return httpx.Response(200, text=listing_html(self.rows, has_next=True), headers={"content-type": "text/html"})


def fixture_connector(settings: Settings, rows: list[dict] | None = None) -> tuple[CpppConnector, FixtureTransport]:
    ft = FixtureTransport(rows if rows is not None else list(CYBER_ROWS))
    http = PoliteHttpClient(settings, delay_seconds=0)
    http.client = httpx.Client(transport=httpx.MockTransport(ft.handler))
    return CpppConnector("cppp", {"listings": ["cpppdata"], "max_pages": 5, "stop_after_known_pages": 1}, settings, http), ft


def make_db(tmp_path: Path):
    from app.db import Base, SessionLocal, configure_engine
    import app.models  # noqa: F401
    from app.pipeline import seed_reference_data

    configure_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    from app import db as db_module
    Base.metadata.create_all(db_module.engine)
    with SessionLocal() as s:
        seed_reference_data(s, get_catalog())
    return SessionLocal

"""CPPP — Central Public Procurement Portal (https://eprocure.gov.in/cppp).

Acquisition method: public HTML listing pages (no official API or RSS feed is published).

What is publicly accessible (verified against the live site):
  * /cppp/latestactivetendersnew/cpppdata  — central active tenders (newest first, 10 per page)
  * /cppp/latestactivetendersnew/mmpdata   — state (MMP) active tenders, same table layout
  Each row: e-Published date, bid closing date, opening date, title / reference / tender ID,
  organisation (or state), corrigendum.

What is NOT automatically accessible:
  * Tender detail pages ("tendersfullview") and their documents are protected by an image CAPTCHA.
    This connector does not attempt to solve or bypass it. Each tender is marked with a blocker so
    an analyst can open the source link, solve the CAPTCHA themselves, and upload the documents via
    the dashboard — after which the full document pipeline runs.
"""
from __future__ import annotations

import base64
import html as html_lib
import re
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.connectors.base import ListingPage, PortalConnector, TenderListing

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://eprocure.gov.in/cppp/latestactivetendersnew/"
LISTINGS = {"cpppdata": "organization", "mmpdata": "location"}  # meaning of the 6th column
DETAIL_BLOCKER = (
    "CPPP tender detail pages and documents are protected by a CAPTCHA. Open the source link, "
    "complete the CAPTCHA yourself, download the tender documents and upload them on the tender page."
)


def parse_cppp_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def listing_url(listing: str, page: int) -> str:
    url = BASE + listing
    if page <= 1:
        return url
    # The portal paginates with a base64-encoded target URL in `?url=`.
    target = base64.b64encode(f"{url}?page={page}".encode()).decode()
    return f"{url}?url={quote(target, safe='')}"


def parse_listing_html(html: str, listing: str = "cpppdata") -> ListingPage:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="table")
    items: list[TenderListing] = []
    if table is None:
        return ListingPage(items, has_next=False)
    sixth = LISTINGS.get(listing, "organization")
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        a = tds[4].find("a")
        if a is None:
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        # Text after the link is "/<reference no>/<tender id>"; the reference itself may contain "/".
        tail = re.sub(r"\s+", " ", tds[4].get_text(" ", strip=True)[len(a.get_text(" ", strip=True)):]).strip()
        tail = tail.lstrip("/").strip()
        ref, tender_id = (tail.rsplit("/", 1) + [None])[:2] if "/" in tail else (None, tail or None)
        corr = tds[6].get_text(" ", strip=True)
        item = TenderListing(
            title=html_lib.unescape(title),
            source_url=a.get("href"),
            portal_tender_id=(tender_id or "").strip() or None,
            reference_number=(ref or "").strip() or None,
            published_at=parse_cppp_datetime(tds[1].get_text()),
            closing_at=parse_cppp_datetime(tds[2].get_text()),
            opening_at=parse_cppp_datetime(tds[3].get_text()),
            corrigendum=None if corr in ("", "--") else corr,
            documents_blocker=DETAIL_BLOCKER,
            raw={"listing": listing, "row": [td.get_text(" ", strip=True) for td in tds]},
        )
        setattr(item, sixth, tds[5].get_text(" ", strip=True) or None)
        items.append(item)
    has_next = soup.find("a", string=re.compile(r"Next", re.I)) is not None
    return ListingPage(items, has_next=has_next)


class CpppConnector(PortalConnector):
    connector_key = "cppp_html"
    acquisition_method = "HTML"

    def listings(self) -> list[str]:
        chosen = list(self.config.get("listings", ["cpppdata"]))
        unknown = [x for x in chosen if x not in LISTINGS]
        if unknown:
            raise ValueError(f"Unsupported CPPP listings {unknown}; supported: {sorted(LISTINGS)}")
        return chosen

    def list_page(self, listing: str, page: int) -> ListingPage:
        resp = self.http.get(listing_url(listing, page), check_captcha=False)
        lp = parse_listing_html(resp.text, listing)
        if not lp.items and re.search(r"What code is in the image", resp.text):
            from app.connectors.base import HumanInterventionRequired
            raise HumanInterventionRequired("CPPP listing returned a CAPTCHA page")
        return lp

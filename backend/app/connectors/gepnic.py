"""GePNIC — NIC Government eProcurement System, used by central eProcurement, defence, PMGSY and
most state/UT portals (…/eprocure/app or …/nicgep/app). One connector serves every instance.

Acquisition method: public HTML (no API or feed is published). Verified against live instances:

  * "Tenders by Organisation" lists every organisation with a live-tender count — public.
  * Each organisation page lists all its live tenders (no pagination): published / closing / opening
    dates, title, reference number, tender ID, organisation chain — public.
  * The tender detail page ("FrontEndViewTender") is public and carries tender value, EMD,
    product category, work description, pre-qualification note, period of work, location,
    critical dates, inviting authority and the names of the tender documents.
  * Downloading the documents requires solving an image CAPTCHA ("Document Download — Enter
    Captcha"). The connector never attempts this; an analyst downloads and uploads them.

Links on these pages are session-bound (Tapestry `sp=` tokens), so a run walks index -> organisation
-> detail inside one HTTP session, and tenders get a lookup hint instead of a permalink.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.connectors.base import (
    DiscoveryResult, HumanInterventionRequired, ListingPage, PortalAccessDenied, PortalConnector, TenderListing,
)
from app.documents.values import parse_amount

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CAPTCHA_PAGE = re.compile(r"Enter\s+Captcha", re.IGNORECASE)
DOCS_BLOCKER = (
    "Tender details were read from the portal, but downloading the documents requires a CAPTCHA. "
    "Open the portal, find the tender (see “How to find it”), download the documents and upload them here."
)


def parse_gepnic_datetime(value: str | None) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _na(value: str | None) -> str | None:
    v = _clean(value)
    return None if v.upper() in ("", "NA", "N/A", "NIL", "-", "--") else v


# ------------------------------------------------------------------ parsers (pure, fixture-tested)
def parse_org_index(html: str) -> list[dict]:
    """[{name, count, href}] from the "Tenders by Organisation" page."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="table")
    out: list[dict] = []
    if table is None:
        return out
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        a = tr.find("a", href=True)
        if len(tds) < 3 or a is None:
            continue
        count = _clean(tds[2].get_text())
        out.append({"name": _clean(tds[1].get_text()), "count": int(count) if count.isdigit() else None,
                    "href": html_lib.unescape(a["href"])})
    return out


_BRACKETS = re.compile(r"\[([^\[\]]*)\]")


def parse_org_tenders(html: str) -> list[TenderListing]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="table")
    items: list[TenderListing] = []
    if table is None:
        return items
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        a = tds[4].find("a", href=True) if len(tds) >= 6 else None
        if a is None:
            continue
        title = _clean(a.get_text(" "))
        title = title[1:-1].strip() if title.startswith("[") and title.endswith("]") else title
        rest = _BRACKETS.findall(_clean(tds[4].get_text(" "))[len(_clean(a.get_text(" "))):])
        ref = _clean(rest[0]) if len(rest) >= 1 else None
        tender_id = _clean(rest[-1]) if len(rest) >= 2 else None
        chain = _clean(tds[5].get_text(" "))
        parts = [p.strip() for p in chain.split("||") if p.strip()]
        items.append(TenderListing(
            title=title, source_url=None, portal_tender_id=tender_id or None, reference_number=ref or None,
            organization=parts[0] if parts else None, department=" › ".join(parts[1:]) or None,
            published_at=parse_gepnic_datetime(tds[1].get_text()),
            closing_at=parse_gepnic_datetime(tds[2].get_text()),
            opening_at=parse_gepnic_datetime(tds[3].get_text()),
            raw={"organisation_chain": chain, "detail_href": html_lib.unescape(a["href"])},
        ))
    return items


def parse_tender_detail(html: str) -> dict[str, str]:
    """Caption -> value pairs (first occurrence wins) plus document names."""
    soup = BeautifulSoup(html, "lxml")
    fields: dict[str, str] = {}
    for cap in soup.find_all("td", class_="td_caption"):
        val = cap.find_next_sibling("td", class_="td_field")
        key = _clean(cap.get_text(" ")).rstrip(":")
        if val is not None and key and key not in fields:
            fields[key] = _clean(val.get_text(" "))
    docs: list[str] = []
    for tid in ("table", "workItemDocumenttable"):
        for t in soup.find_all("table", id=tid):
            for cell in t.find_all(["a", "td"]):
                name = _clean(cell.get_text())
                if re.fullmatch(r"[^\s/\\]{1,150}\.(pdf|docx?|xlsx?|zip|rar|csv|txt|jpe?g|png)", name, re.IGNORECASE):
                    docs.append(name)
    fields["_documents"] = "; ".join(dict.fromkeys(docs))
    return fields


def apply_detail(item: TenderListing, f: dict[str, str]) -> TenderListing:
    if not f.get("Tender ID"):
        raise ValueError("detail page has no Tender ID (session expired or layout changed)")
    value = parse_amount(f.get("Tender Value in ₹", "") or "")
    emd = parse_amount(f.get("EMD Amount in ₹", "") or "")
    item.tender_value_inr = value if value and value > 0 else None
    item.emd_inr = emd if emd and emd > 0 else None
    cat = _na(f.get("Product Category"))
    sub = _na(f.get("Sub category"))
    item.category = " / ".join(x for x in (cat, sub) if x) or None
    item.tender_type = " / ".join(x for x in (_na(f.get("Tender Type")), _na(f.get("Tender Category"))) if x) or None
    loc = _na(f.get("Location"))
    pin = _na(f.get("Pincode"))
    item.location = ", ".join(x for x in (loc, pin) if x) or item.location
    item.closing_at = parse_gepnic_datetime(f.get("Bid Submission End Date")) or item.closing_at
    item.published_at = parse_gepnic_datetime(f.get("Published Date")) or item.published_at
    item.opening_at = parse_gepnic_datetime(f.get("Bid Opening Date")) or item.opening_at
    authority = " — ".join(x for x in (_na(f.get("Name")), _na(f.get("Address"))) if x)
    item.contact_info = authority or None
    lines = [
        ("Title", f.get("Title")), ("Work description", f.get("Work Description")),
        ("Pre-qualification", f.get("NDA/Pre Qualification")), ("Tender category", f.get("Tender Category")),
        ("Product category", item.category), ("Form of contract", f.get("Form Of Contract")),
        ("Tender value (INR)", f.get("Tender Value in ₹")), ("EMD (INR)", f.get("EMD Amount in ₹")),
        ("Period of work (days)", f.get("Period Of Work(Days)")), ("Bid validity (days)", f.get("Bid Validity(Days)")),
        ("Location", item.location), ("Pre-bid meeting", f.get("Pre Bid Meeting Date")),
        ("Tender documents", f.get("_documents")),
    ]
    item.portal_text = "\n".join(f"{k}: {v}" for k, v in lines if _na(v))
    item.documents_blocker = DOCS_BLOCKER if f.get("_documents") else None
    item.raw["document_names"] = f.get("_documents")
    item.detail_fetched = True
    return item


# ------------------------------------------------------------------ connector
class GepnicConnector(PortalConnector):
    connector_key = "gepnic_html"
    acquisition_method = "HTML"

    @property
    def base(self) -> str:
        url = self.config.get("base_url")
        if not url:
            raise ValueError("GePNIC portal config needs base_url, e.g. https://etenders.gov.in/eprocure/app")
        return url.rstrip("/")

    @property
    def origin(self) -> str:
        return re.match(r"https?://[^/]+", self.base).group(0)  # type: ignore[union-attr]

    def _abs(self, href: str) -> str:
        return href if href.startswith("http") else self.origin + href

    def _get(self, url: str) -> str:
        resp = self.http.get(url, check_captcha=False)
        if CAPTCHA_PAGE.search(resp.text) and "td_caption" not in resp.text and 'id="table"' not in resp.text:
            raise HumanInterventionRequired(f"CAPTCHA required at {url}")
        return resp.text

    def list_page(self, listing: str, page: int) -> ListingPage:  # pragma: no cover - discover() is overridden
        raise NotImplementedError("GePNIC is crawled by organisation; see discover()")

    def _org_selected(self, name: str) -> bool:
        inc = self.config.get("include_orgs")
        exc = self.config.get("exclude_orgs")
        if inc and not re.search(inc, name, re.IGNORECASE):
            return False
        return not (exc and re.search(exc, name, re.IGNORECASE))

    def lookup_hint(self, item: TenderListing) -> str:
        host = self.origin.split("://", 1)[1]
        return (f"On {host}, open “Tenders by Organisation”, choose “{item.organization}”, "
                f"then open Tender ID {item.portal_tender_id}.")

    def discover(self, is_known: Callable[[str], bool],
                 wants_detail: Callable[[TenderListing], bool] | None = None) -> DiscoveryResult:
        result = DiscoveryResult()
        mode = self.config.get("detail_mode", "candidates")  # candidates | all | none
        max_details = int(self.config.get("max_details_per_run", 300))
        details = 0
        try:
            orgs = parse_org_index(self._get(f"{self.base}?page=FrontEndTendersByOrganisation&service=page"))
            result.pages_fetched += 1
        except (HumanInterventionRequired, PortalAccessDenied) as e:
            result.blockers.append(str(e))
            return result
        except Exception as e:
            result.errors.append(f"organisation index: {type(e).__name__}: {e}")
            return result
        if not orgs:
            result.errors.append("organisation index returned no organisations (layout change?)")
            return result

        for org in orgs:
            if not self._org_selected(org["name"]) or org["count"] == 0:
                continue
            try:
                items = parse_org_tenders(self._get(self._abs(org["href"])))
                result.pages_fetched += 1
            except HumanInterventionRequired as e:
                result.blockers.append(f"{org['name']}: {e}")
                break  # the portal is challenging us; stop the run politely
            except Exception as e:
                result.errors.append(f"{org['name']}: {type(e).__name__}: {e}")
                continue
            for item in items:
                item.source_url = f"{self.base}?page=FrontEndTendersByOrganisation&service=page"
                item.lookup_hint = self.lookup_hint(item)
                want = mode == "all" or (mode == "candidates" and wants_detail is not None and wants_detail(item))
                if want and details < max_details:
                    try:
                        apply_detail(item, parse_tender_detail(self._get(self._abs(item.raw["detail_href"]))))
                        details += 1
                    except HumanInterventionRequired as e:
                        result.blockers.append(f"detail {item.portal_tender_id}: {e}")
                    except Exception as e:
                        result.errors.append(f"detail {item.portal_tender_id}: {type(e).__name__}: {e}")
                item.raw.pop("detail_href", None)  # session-bound; useless after this run
                result.items.append(item)
        result.stopped_reason = f"{len(orgs)} organisations scanned, {details} detail pages read"
        if details >= max_details:
            result.stopped_reason += f" (detail cap {max_details} reached; remaining candidates next run)"
        return result

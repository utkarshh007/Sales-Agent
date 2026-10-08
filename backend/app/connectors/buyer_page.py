"""Buyer tender pages — the "Tenders" / "Procurement" pages that banks, regulators, PSUs and
research bodies publish on their own websites (Phase 3).

These pages are usually an HTML table (sometimes built by JavaScript) with a title, dates and a link
to the notice PDF or a detail page. One configurable connector reads them all:

  config = {
    "pages": [{"url": "...", "organization": "State Bank of India"}],
    "render": "http" | "browser",          # browser = headless Chromium for JavaScript-built lists
    "wait_for": "table",                   # optional CSS selector to await in browser mode
    "columns": {"title": "description"},   # optional header-regex overrides per field
    "reference_regex": "^(?P<ref>[^:]{4,80}):",   # optional: pull the reference out of the title
    "follow_detail": false,                # open each candidate's detail page to find documents
    "skip_closed": true,
  }

Documents linked straight from the page (no CAPTCHA) are downloaded and run through the full
document pipeline — but only for candidates that pass the engine's cheap title screen.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from app.connectors.base import (
    DiscoveryResult, DocumentRef, HumanInterventionRequired, ListingPage, PortalAccessDenied, PortalConnector,
    TenderListing,
)
from app.connectors.render import make_renderer
from app.db import utcnow

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

DEFAULT_COLUMNS: dict[str, str] = {
    "title": r"description|title|subject|name of (the )?(work|tender)|particulars|tender (details|name)|work",
    "reference": r"advt|advertisement|ref(erence)?|tender (no|number|id)|nit no|rfp no",
    "published": r"start date|publish|release|tender date|date of (issue|publication)|issue date|uploaded",
    "closing": r"end date|last date|closing|due date|submission",
    "location": r"location|centre|center|office|unit|region|circle|branch",
    "organization": r"advertiser|organi[sz]ation|department|issued by",
}
# A link counts as a document only with a real file signal: an extension, a document-serving endpoint,
# or a file parameter. A bare "download" in the URL is usually a site's "Downloads" navigation page.
DOC_LINK = re.compile(
    r"\.(pdf|docx?|xlsx?|zip|rar)(\?|#|$)|viewpdf|getdocument|downloadfile|/documents?/\d|[?&](file|doc|filename)=",
    re.IGNORECASE)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(rf"\b\d{{1,2}}[-/ .](?:{_MONTHS})[a-z]*[-/ .,]+\d{{4}}(?:\s*[,-]?\s*\d{{1,2}}:\d{{2}}(?::\d{{2}})?\s*(?:[AP]M)?)?", re.I),
     ["%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%d-%b-%Y"]),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"), ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]),
    (re.compile(rf"\b(?:{_MONTHS})[a-z]*\.? \d{{1,2}},? \d{{4}}(?:\s*-?\s*\d{{1,2}}:\d{{2}})?", re.I),
     ["%b %d %Y %H:%M", "%b %d %Y"]),
    (re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}(?:\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?", re.I),
     ["%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M", "%d-%m-%Y"]),  # Indian dd-mm-yyyy
]


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _normalise_date_text(s: str) -> str:
    s = re.sub(r"(?i)\b(sept)\b", "Sep", s)
    s = re.sub(r"[/ .]", "-", s, count=2) if re.match(r"^\d{1,2}[/ .]", s) else s
    s = re.sub(r"(?i)^([a-z]{3})[a-z]*\.?", r"\1", s)  # "September 28," -> "Sep 28,"
    s = re.sub(r"(\d{4})\s*[,-]\s*(\d{1,2}:)", r"\1 \2", s)
    return re.sub(r"[,]", " ", re.sub(r"\s+", " ", s)).replace("  ", " ").strip()


def find_dates(text: str) -> list[datetime]:
    """Every date in a cell, in order of appearance (handles ranges like "A - to B")."""
    found: list[tuple[int, datetime]] = []
    taken: list[tuple[int, int]] = []
    for rx, fmts in DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            if any(a <= m.start() < b for a, b in taken):
                continue
            raw = _normalise_date_text(m.group(0))
            for fmt in fmts:
                try:
                    dt = datetime.strptime(raw.replace("-", " ") if "%b %d" in fmt else raw, fmt)
                    found.append((m.start(), dt.replace(tzinfo=IST)))
                    taken.append((m.start(), m.end()))
                    break
                except ValueError:
                    continue
    return [dt for _, dt in sorted(found, key=lambda x: x[0])]


def _header_cells(table: Tag) -> tuple[list[str], Tag | None]:
    for tr in table.find_all("tr")[:3]:
        cells = tr.find_all(["th", "td"])
        texts = [_clean(c.get_text(" ")) for c in cells]
        if len(cells) >= 2 and (tr.find("th") or all(len(t) < 60 for t in texts)) and any(texts):
            return texts, tr
    return [], None


def map_columns(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, int]:
    patterns = {**DEFAULT_COLUMNS, **(overrides or {})}
    mapping: dict[str, int] = {}
    for field in ("closing", "published", "reference", "location", "organization", "title"):
        rx = re.compile(patterns[field], re.IGNORECASE)
        for i, h in enumerate(headers):
            if i not in mapping.values() and rx.search(h):
                mapping[field] = i
                break
    return mapping


def _links(cell: Tag | None, base_url: str) -> list[tuple[str, str]]:
    if cell is None:
        return []
    return [(_clean(a.get_text(" ")), urljoin(base_url, a["href"])) for a in cell.find_all("a", href=True)
            if not a["href"].startswith(("#", "javascript:", "mailto:"))]


def _doc_name(text: str, url: str) -> str:
    base = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    if re.search(r"\.(pdf|docx?|xlsx?|zip|rar)$", base, re.I):
        return base[:150]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"\(.*?\)", "", text)).strip("_")[:80] or "tender_document"
    return f"{stem}.pdf"


def extract_tenders(html: str, base_url: str, cfg: dict[str, Any], organization: str | None = None) -> list[TenderListing]:
    """Turn every recognisable tender table on the page into listings."""
    soup = BeautifulSoup(html, "lxml")
    out: list[TenderListing] = []
    ref_rx = re.compile(cfg["reference_regex"]) if cfg.get("reference_regex") else None
    for table in soup.select(cfg.get("table_selector", "table")):
        headers, header_row = _header_cells(table)
        cols = map_columns(headers, cfg.get("columns"))
        if "title" not in cols and "reference" not in cols:
            continue
        for tr in table.find_all("tr"):
            if tr is header_row:
                continue
            cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
            if len(cells) < len(headers) - 1 or len(cells) < 2:
                continue
            get = lambda f: cells[cols[f]] if f in cols and cols[f] < len(cells) else None  # noqa: E731
            title_cell, ref_cell = get("title"), get("reference")
            title = _clean(title_cell.get_text(" ")) if title_cell is not None else ""
            reference = _clean(ref_cell.get_text(" ")) if ref_cell is not None else None
            if ref_rx and title and not reference:
                m = ref_rx.search(title)
                if m:
                    reference = _clean(m.group("ref"))
                    title = _clean(title[m.end():]) or title
            if not title:
                title = reference or ""
            if len(title) < 5:
                continue
            row_links = [link for c in cells for link in _links(c, base_url)]
            docs = [(t, u) for t, u in row_links if DOC_LINK.search(u)]
            detail = next((u for t, u in _links(title_cell, base_url) if not DOC_LINK.search(u)), None) \
                or next((u for t, u in row_links if not DOC_LINK.search(u) and urlparse(u).netloc == urlparse(base_url).netloc), None)
            pub_dates = find_dates(get("published").get_text(" ")) if get("published") is not None else []
            close_dates = find_dates(get("closing").get_text(" ")) if get("closing") is not None else []
            published = pub_dates[0] if pub_dates else None
            closing = close_dates[-1] if close_dates else (pub_dates[-1] if len(pub_dates) >= 2 else None)
            if closing is not None and (closing.hour, closing.minute) == (0, 0):
                closing = closing.replace(hour=23, minute=59)  # a bare date means "until the end of that day"
            org_cell = get("organization")
            org = _clean(org_cell.get_text(" ")).split(" / ")[-1] if org_cell is not None else None
            loc_cell = get("location")
            ident = reference or hashlib.sha1(f"{title}|{detail or (docs[0][1] if docs else '')}".encode()).hexdigest()[:16]
            item = TenderListing(
                title=title, source_url=detail or (docs[0][1] if docs else base_url), portal_tender_id=ident[:200],
                reference_number=reference, organization=organization or org or None,
                department=org if organization and org and org != organization else None,
                location=_clean(loc_cell.get_text(" ")) if loc_cell is not None else None,
                published_at=published, closing_at=closing,
                raw={"page": base_url, "detail_url": detail,
                     "doc_links": [{"name": _doc_name(t, u), "url": u} for t, u in docs]},
            )
            out.append(item)
    return out


def extract_document_links(html: str, base_url: str) -> list[DocumentRef]:
    soup = BeautifulSoup(html, "lxml")
    for chrome in soup(["nav", "header", "footer", "script", "style"]):
        chrome.decompose()  # site navigation is not part of the tender
    refs: list[DocumentRef] = []
    for text, url in _links(soup, base_url):
        if DOC_LINK.search(url) and urlparse(url).scheme in ("http", "https"):
            refs.append(DocumentRef(url=url, filename=_doc_name(text, url)))
    return list({r.url: r for r in refs}.values())


class BuyerPageConnector(PortalConnector):
    connector_key = "buyer_page"
    acquisition_method = "HTML"  # or BROWSER, per portal row

    def list_page(self, listing: str, page: int) -> ListingPage:  # pragma: no cover - discover() is overridden
        raise NotImplementedError

    def pages(self) -> list[dict]:
        pages = self.config.get("pages") or []
        if not pages:
            raise ValueError("buyer_page config needs at least one entry in 'pages'")
        for p in pages:
            if urlparse(p.get("url", "")).scheme not in ("http", "https"):
                raise ValueError(f"invalid page url {p.get('url')!r}")
        return pages

    def discover(self, is_known: Callable[[str], bool],
                 wants_detail: Callable[..., bool] | None = None) -> DiscoveryResult:
        result = DiscoveryResult()
        renderer = make_renderer(self.config, self.settings, self.http)
        skip_closed = self.config.get("skip_closed", True)
        follow = bool(self.config.get("follow_detail"))
        # "title": only titles with a possible match get documents (default).
        # "documents": the page carries no subject (e.g. only an advert number), so every new open
        # tender's documents are fetched and the deterministic pre-filter screens their text.
        require_signal = self.config.get("screen", "title") != "documents"
        max_details = int(self.config.get("max_details_per_run", 100))
        details = 0
        now = utcnow()
        try:
            for page in self.pages():
                try:
                    html = renderer.html(page["url"])
                    result.pages_fetched += 1
                except (HumanInterventionRequired, PortalAccessDenied) as e:
                    result.blockers.append(str(e))
                    continue
                except Exception as e:
                    result.errors.append(f"{page['url']}: {type(e).__name__}: {e}")
                    continue
                items = extract_tenders(html, page["url"], self.config, page.get("organization"))
                if not items:
                    result.errors.append(f"{page['url']}: no tender table recognised (layout change?)")
                for item in items:
                    if skip_closed and item.closing_at is not None and item.closing_at < now:
                        continue
                    if wants_detail is not None and wants_detail(item, require_signal) and details < max_details:
                        try:
                            docs = [DocumentRef(d["url"], d["name"]) for d in item.raw.get("doc_links", [])]
                            if follow and item.raw.get("detail_url"):
                                detail_html = renderer.html(item.raw["detail_url"])
                                docs += extract_document_links(detail_html, item.raw["detail_url"])
                                detail_dates = find_dates(BeautifulSoup(detail_html, "lxml").get_text(" "))
                                if item.closing_at is None and detail_dates:
                                    item.closing_at = max(detail_dates)
                            item.documents = list({d.url: d for d in docs}.values())
                            item.detail_fetched = True
                            details += 1
                        except (HumanInterventionRequired, PortalAccessDenied) as e:
                            result.blockers.append(f"detail {item.portal_tender_id}: {e}")
                        except Exception as e:
                            result.errors.append(f"detail {item.portal_tender_id}: {type(e).__name__}: {e}")
                    result.items.append(item)
        finally:
            renderer.close()
        result.stopped_reason = (f"{len(self.pages())} page(s) read with {self.config.get('render', 'http')} rendering, "
                                 f"{len(result.items)} open tenders, {details} candidates with documents")
        return result

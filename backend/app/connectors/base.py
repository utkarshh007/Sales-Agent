"""Portal connector contract (section 7).

A connector only knows how to talk to one portal: list tenders, fetch details, find and download
documents. Everything after that (rules, LLM, matching, scoring) is portal-agnostic, so adding a
portal never touches the intelligence engine.

Connectors must never bypass CAPTCHA, anti-bot protection or access controls. When a portal needs a
human, raise HumanInterventionRequired — the engine logs the blocker and moves on.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

CAPTCHA_MARKERS = re.compile(
    r"captcha|What code is in the image|g-recaptcha|hcaptcha|cf-challenge|are you a robot|verify you are human",
    re.IGNORECASE,
)


class HumanInterventionRequired(Exception):
    """The portal needs a human (CAPTCHA, OTP, DSC login...). Never worked around automatically."""


class PortalAccessDenied(Exception):
    """robots.txt or the portal's terms disallow automated access to this URL."""


def is_public_host(host: str) -> bool:
    """False when a hostname resolves to a private, loopback, link-local or otherwise non-public
    address. Portal and document URLs come from web pages and admins, so every fetch is checked to
    keep the server from being used to reach internal services (SSRF). Unresolvable names are let
    through: the connection itself will fail."""
    import ipaddress
    import socket

    host = (host or "").split(":")[0].strip("[]")
    if not host:
        return False
    try:
        candidates = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            candidates = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except (socket.gaierror, UnicodeError, OSError):
            return True
    return all(ip.is_global and not ip.is_multicast for ip in candidates)


def ensure_public_url(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise PortalAccessDenied(f"only http(s) URLs may be fetched, not {url!r}")
    if not is_public_host(parts.hostname or ""):
        raise PortalAccessDenied(f"refusing to fetch {url}: it points at a private or internal network address")


@dataclass
class DocumentRef:
    url: str
    filename: str


@dataclass
class TenderListing:
    title: str
    source_url: str | None
    portal_tender_id: str | None = None
    reference_number: str | None = None
    organization: str | None = None
    department: str | None = None
    location: str | None = None
    category: str | None = None
    tender_type: str | None = None
    published_at: datetime | None = None
    closing_at: datetime | None = None
    opening_at: datetime | None = None
    tender_value_inr: int | None = None
    emd_inr: int | None = None
    contact_info: str | None = None
    corrigendum: str | None = None
    documents: list[DocumentRef] = field(default_factory=list)
    documents_blocker: str | None = None  # set when documents need a human to retrieve
    # Portal-published tender text (work description, pre-qualification, document names...).
    # Used as evidence by the rule engine and the LLM; it is not a downloaded document.
    portal_text: str | None = None
    # True when detail-level fields (value, EMD, category, portal_text...) were collected this run.
    # Connectors whose listing already carries the full record should set it at construction.
    detail_fetched: bool = False
    # How a human finds this tender on the portal when it has no stable permalink.
    lookup_hint: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def identity_key(self) -> str:
        return self.portal_tender_id or self.reference_number or self.source_url or self.title

    def content_signature(self) -> dict[str, Any]:
        """Listing-level fields whose change counts as a material update (triggers re-processing)."""
        return {
            "title": self.title,
            "reference_number": self.reference_number,
            "closing_at": self.closing_at.isoformat() if self.closing_at else None,
            "opening_at": self.opening_at.isoformat() if self.opening_at else None,
            "corrigendum": self.corrigendum,
        }

    def detail_signature(self) -> dict[str, Any] | None:
        """Detail-level fields; only comparable when they were actually fetched this run."""
        if not self.detail_fetched:
            return None
        return {
            "tender_value_inr": self.tender_value_inr,
            "emd_inr": self.emd_inr,
            "category": self.category,
            "portal_text": self.portal_text,
            "documents": sorted(d.url for d in self.documents),
        }


@dataclass
class ListingPage:
    items: list[TenderListing]
    has_next: bool


@dataclass
class DiscoveryResult:
    items: list[TenderListing] = field(default_factory=list)
    pages_fetched: int = 0
    blockers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped_reason: str = ""


class PoliteHttpClient:
    """HTTP client that respects robots.txt, rate-limits per host, retries transient errors and
    refuses to continue past CAPTCHA/anti-bot pages."""

    def __init__(self, settings: Settings, delay_seconds: float | None = None, respect_robots: bool = True):
        self.settings = settings
        self.delay = settings.PORTAL_REQUEST_DELAY_SECONDS if delay_seconds is None else delay_seconds
        self.respect_robots = respect_robots
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.client = httpx.Client(
            headers={"User-Agent": settings.HTTP_USER_AGENT, "Accept-Language": "en-IN,en;q=0.8"},
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            # every request, including each redirect hop, must target a public address
            event_hooks={"request": [lambda request: ensure_public_url(str(request.url))]},
        )

    def close(self) -> None:
        self.client.close()

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        ensure_public_url(url)
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self.client.get(origin + "/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None  # no robots.txt => no restrictions declared
            except httpx.HTTPError:
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.settings.HTTP_USER_AGENT, url)

    def get(self, url: str, *, check_captcha: bool = True, **kw) -> httpx.Response:
        if not self._allowed(url):
            raise PortalAccessDenied(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(3):
            self._throttle(host)
            try:
                resp = self.client.get(url, **kw)
                if resp.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                if check_captcha and "html" in resp.headers.get("content-type", "") and CAPTCHA_MARKERS.search(resp.text):
                    raise HumanInterventionRequired(f"CAPTCHA / anti-bot challenge at {url}")
                return resp
            except HumanInterventionRequired:
                raise
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code < 500 and e.response.status_code != 429:
                    raise
                last_exc = e
            except httpx.TransportError as e:
                last_exc = e
            time.sleep(min(30, 2 ** (attempt + 1)))
        raise last_exc  # type: ignore[misc]

    def download(self, url: str, dest: Path, max_bytes: int) -> tuple[Path, str | None, int]:
        if not self._allowed(url):
            raise PortalAccessDenied(f"robots.txt disallows {url}")
        self._throttle(urlparse(url).netloc)
        size = 0
        with self.client.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type")
            if ctype and "html" in ctype:
                body = resp.read()
                if CAPTCHA_MARKERS.search(body.decode("utf-8", "replace")):
                    raise HumanInterventionRequired(f"Document download at {url} is behind a CAPTCHA")
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise ValueError(f"Document exceeds size limit ({max_bytes} bytes): {url}")
                    fh.write(chunk)
        return dest, ctype, size


class PortalConnector(ABC):
    """Subclass, set the ClassVars, implement list_page(). Override fetch_detail() and
    download_document() when the portal exposes details/documents without human intervention."""

    connector_key: ClassVar[str]
    acquisition_method: ClassVar[str]  # API | FEED | HTML | BROWSER | BROWSER_AUTH

    def __init__(self, portal_code: str, config: dict[str, Any], settings: Settings, http: PoliteHttpClient | None = None):
        self.portal_code = portal_code
        self.config = config or {}
        self.settings = settings
        self.http = http or PoliteHttpClient(settings, delay_seconds=self.config.get("request_delay_seconds"))

    # -- required
    @abstractmethod
    def list_page(self, listing: str, page: int) -> ListingPage: ...

    def listings(self) -> list[str]:
        """A portal may expose several listing feeds (e.g. central vs state)."""
        return list(self.config.get("listings", ["default"]))

    # -- optional
    def fetch_detail(self, item: TenderListing) -> TenderListing:
        return item

    def download_document(self, ref: DocumentRef, dest_dir: Path) -> tuple[Path, str | None, int]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", ref.filename)[:150] or "document"
        return self.http.download(ref.url, dest_dir / safe, self.settings.MAX_DOCUMENT_BYTES)

    def fingerprint(self, item: TenderListing) -> str:
        return hashlib.sha256(f"{self.portal_code}|{item.identity_key()}".encode()).hexdigest()

    # -- discovery loop (shared)
    def discover(self, is_known: Callable[[str], bool],
                 wants_detail: Callable[[TenderListing], bool] | None = None) -> DiscoveryResult:
        """Walk listing pages newest-first; stop when N consecutive pages contain nothing new
        (incremental discovery) or the page cap is reached.

        `wants_detail` lets the engine say which listings deserve a (more expensive) detail fetch —
        typically new or changed tenders whose title shows a possible match. Connectors that have
        detail pages call it; others ignore it."""
        result = DiscoveryResult()
        max_pages = int(self.config.get("max_pages", self.settings.DISCOVERY_MAX_PAGES))
        stop_after = int(self.config.get("stop_after_known_pages", self.settings.DISCOVERY_STOP_AFTER_KNOWN_PAGES))
        for listing in self.listings():
            known_streak = 0
            for page in range(1, max_pages + 1):
                try:
                    lp = self.list_page(listing, page)
                except HumanInterventionRequired as e:
                    result.blockers.append(f"{listing} p{page}: {e}")
                    break
                except PortalAccessDenied as e:
                    result.blockers.append(str(e))
                    break
                except Exception as e:  # keep other listings going
                    log.exception("listing page failed")
                    result.errors.append(f"{listing} p{page}: {type(e).__name__}: {e}")
                    break
                result.pages_fetched += 1
                new_items = [i for i in lp.items if not is_known(self.fingerprint(i))]
                result.items.extend(lp.items)
                known_streak = known_streak + 1 if not new_items else 0
                if known_streak >= stop_after:
                    result.stopped_reason = f"{listing}: {stop_after} consecutive pages with no new tenders"
                    break
                if not lp.has_next:
                    break
            else:
                result.stopped_reason = f"{listing}: reached max_pages={max_pages}"
        return result

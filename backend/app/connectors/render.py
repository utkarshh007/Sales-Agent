"""Page renderers: plain HTTP, or a real headless browser for pages that build their tender lists
with JavaScript (Phase 3).

Both enforce the same access policy: robots.txt is honoured, requests are rate-limited per host, and
CAPTCHA / anti-bot / access-control responses stop the run (HumanInterventionRequired or
PortalAccessDenied). The browser is never used to get past a block that a plain client hits — a
403/429 from a firewall is treated as "not permitted", not as a reason to try harder.

Browser support needs Playwright:  pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import logging
from types import TracebackType
from typing import Protocol
from urllib.parse import urlparse

from app.config import Settings
from app.connectors.base import CAPTCHA_MARKERS, HumanInterventionRequired, PoliteHttpClient, PortalAccessDenied

log = logging.getLogger(__name__)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


class Renderer(Protocol):
    def html(self, url: str) -> str: ...
    def close(self) -> None: ...


class HttpRenderer:
    def __init__(self, http: PoliteHttpClient):
        self.http = http

    def html(self, url: str) -> str:
        resp = self.http.get(url, check_captcha=False)
        if CAPTCHA_MARKERS.search(resp.text) and not _has_tabular_content(resp.text):
            raise HumanInterventionRequired(f"CAPTCHA / anti-bot challenge at {url}")
        return resp.text

    def close(self) -> None:
        pass


class BrowserRenderer:
    """One headless Chromium per discovery run. Pages are rendered with JavaScript, then handed to the
    same HTML parsers as the HTTP path."""

    def __init__(self, settings: Settings, http: PoliteHttpClient, wait_for: str | None = None,
                 timeout_ms: int = 45_000):
        self.settings = settings
        self.http = http  # used for robots.txt checks and per-host throttling
        self.wait_for = wait_for
        self.timeout_ms = timeout_ms
        self._pw = self._browser = self._context = None

    def _start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("Browser rendering needs Playwright: pip install playwright && "
                               "python -m playwright install chromium") from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=self.settings.HTTP_USER_AGENT,
                                                  java_script_enabled=True, accept_downloads=False)
        self._context.route("**/*", self._route)

    @staticmethod
    def _route(route) -> None:  # noqa: ANN001 - playwright Route
        """Skip heavy resources and never let page scripts reach private/internal addresses (SSRF)."""
        req = route.request
        if req.resource_type in BLOCKED_RESOURCE_TYPES:
            return route.abort()
        try:
            from app.connectors.base import ensure_public_url
            if req.url.startswith(("http://", "https://")):
                ensure_public_url(req.url)
        except PortalAccessDenied:
            return route.abort()
        return route.continue_()

    def html(self, url: str) -> str:
        if not self.http._allowed(url):
            raise PortalAccessDenied(f"robots.txt disallows {url}")
        self.http._throttle(urlparse(url).netloc)
        self._start()
        page = self._context.new_page()  # type: ignore[union-attr]
        try:
            resp = page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            status = resp.status if resp is not None else 0
            if status in (401, 403, 407, 429):
                raise PortalAccessDenied(f"{url} answered HTTP {status}; treating as not permitted")
            if status >= 400:
                raise RuntimeError(f"{url} answered HTTP {status}")
            if self.wait_for:
                page.wait_for_selector(self.wait_for, timeout=self.timeout_ms)
            content = page.content()
            if CAPTCHA_MARKERS.search(content) and not _has_tabular_content(content):
                raise HumanInterventionRequired(f"CAPTCHA / anti-bot challenge at {url}")
            return content
        finally:
            page.close()

    def close(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # pragma: no cover - best effort shutdown
                log.debug("browser close failed", exc_info=True)
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = None

    def __enter__(self) -> BrowserRenderer:
        return self

    def __exit__(self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


def _has_tabular_content(html: str) -> bool:
    """Many portals show a small CAPTCHA search box beside real content; only a page *without*
    content is treated as a challenge."""
    return html.count("<tr") >= 3


def make_renderer(config: dict, settings: Settings, http: PoliteHttpClient) -> Renderer:
    mode = config.get("render", "http")
    if mode == "browser":
        return BrowserRenderer(settings, http, wait_for=config.get("wait_for"))
    if mode == "http":
        return HttpRenderer(http)
    raise ValueError(f"render must be 'http' or 'browser', not {mode!r}")

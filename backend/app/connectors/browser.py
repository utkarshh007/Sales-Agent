"""Browser-automation support for portals that only render tenders with JavaScript (Phase 3).

Use only where the portal's terms permit automated access, and — for BROWSER_AUTH portals — only with
credentials the company is authorised to use (resolved from env vars named in
portal_credentials_metadata, never stored in code). CAPTCHA / OTP / DSC prompts are never solved
automatically: they raise HumanInterventionRequired and discovery continues with other portals.

Playwright is an optional dependency:  pip install playwright && playwright install chromium
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from app.connectors.base import CAPTCHA_MARKERS, HumanInterventionRequired, PortalConnector


class BrowserPortalConnector(PortalConnector):
    acquisition_method = "BROWSER"

    @contextmanager
    def browser_page(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("Playwright is not installed; see app/connectors/browser.py") from e
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=self.settings.HTTP_USER_AGENT)
            page = context.new_page()
            try:
                yield page
            finally:
                context.close()
                browser.close()

    def goto(self, page: Any, url: str) -> str:
        page.goto(url, wait_until="networkidle", timeout=60_000)
        html = page.content()
        if CAPTCHA_MARKERS.search(html):
            raise HumanInterventionRequired(f"CAPTCHA / anti-bot challenge at {url}")
        return html

    def credentials(self, username_env: str | None, secret_env: str | None) -> tuple[str, str]:
        user = os.environ.get(username_env or "", "")
        secret = os.environ.get(secret_env or "", "")
        if not user or not secret:
            raise HumanInterventionRequired("Portal credentials are not configured in the environment")
        return user, secret

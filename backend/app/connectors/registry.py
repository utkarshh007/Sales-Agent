"""Connector registry. To add a portal: implement a PortalConnector subclass, register it here and
insert a `portals` row (or add it to DEFAULT_PORTALS). The intelligence engine is untouched."""
from __future__ import annotations

from app.config import Settings
from app.connectors.base import PortalConnector
from app.connectors.cppp import CpppConnector

CONNECTORS: dict[str, type[PortalConnector]] = {
    CpppConnector.connector_key: CpppConnector,
}

DEFAULT_PORTALS = [
    {
        "code": "cppp",
        "name": "Central Public Procurement Portal (CPPP)",
        "connector": "cppp_html",
        "base_url": "https://eprocure.gov.in/cppp/",
        "acquisition_method": "HTML",
        "schedule_minutes": 45,
        # page limits come from DISCOVERY_* settings unless overridden here per portal
        "config": {"listings": ["cpppdata"]},
        "blocker": "Detail pages and documents are CAPTCHA-protected; documents must be uploaded by an analyst.",
    },
    {
        # Pseudo-portal for tenders an analyst enters by hand (e.g. from email, partner, or a portal
        # without a connector). No discovery runs for it.
        "code": "manual",
        "name": "Manual entry / upload",
        "connector": "manual",
        "base_url": "",
        "acquisition_method": "MANUAL",
        "schedule_minutes": 0,
        "config": {},
        "blocker": None,
    },
]


def build_connector(connector_key: str, portal_code: str, config: dict, settings: Settings) -> PortalConnector:
    cls = CONNECTORS.get(connector_key)
    if cls is None:
        raise KeyError(f"No connector registered for '{connector_key}'")
    return cls(portal_code, config, settings)

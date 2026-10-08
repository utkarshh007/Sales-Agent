"""Connector registry. To add a portal: implement a PortalConnector subclass, register it here and
insert a `portals` row (or add it to DEFAULT_PORTALS). The intelligence engine is untouched."""
from __future__ import annotations

from app.config import Settings
from app.connectors.base import PortalConnector
from app.connectors.buyer_page import BuyerPageConnector
from app.connectors.cppp import CpppConnector
from app.connectors.gepnic import GepnicConnector

CONNECTORS: dict[str, type[PortalConnector]] = {
    CpppConnector.connector_key: CpppConnector,
    GepnicConnector.connector_key: GepnicConnector,
    BuyerPageConnector.connector_key: BuyerPageConnector,
}

# GePNIC instances verified reachable with the standard "Tenders by Organisation" layout.
# code -> (display name, base URL, enabled by default)
GEPNIC_PORTALS: dict[str, tuple[str, str, bool]] = {
    "gepnic_cpse": ("Central eProcurement — CPSEs (eprocure.gov.in)", "https://eprocure.gov.in/eprocure/app", True),
    "gepnic_central": ("Central eProcurement — Ministries (etenders.gov.in)", "https://etenders.gov.in/eprocure/app", True),
    "gepnic_defence": ("Defence eProcurement (defproc.gov.in)", "https://defproc.gov.in/nicgep/app", True),
    "gepnic_pmgsy": ("PMGSY eProcurement", "https://pmgsytenders.gov.in/nicgep/app", False),
    "gepnic_maharashtra": ("Maharashtra eTenders", "https://mahatenders.gov.in/nicgep/app", False),
    "gepnic_tamilnadu": ("Tamil Nadu eTenders", "https://tntenders.gov.in/nicgep/app", False),
    "gepnic_westbengal": ("West Bengal eTenders", "https://wbtenders.gov.in/nicgep/app", False),
    "gepnic_kerala": ("Kerala eTenders", "https://etenders.kerala.gov.in/nicgep/app", False),
    "gepnic_punjab": ("Punjab eProcurement", "https://eproc.punjab.gov.in/nicgep/app", False),
    "gepnic_himachal": ("Himachal Pradesh eTenders", "https://hptenders.gov.in/nicgep/app", False),
    "gepnic_uttarakhand": ("Uttarakhand eTenders", "https://uktenders.gov.in/nicgep/app", False),
    "gepnic_uttarpradesh": ("Uttar Pradesh eTender", "https://etender.up.nic.in/nicgep/app", False),
    "gepnic_delhi": ("Delhi Government eProcurement", "https://govtprocurement.delhi.gov.in/nicgep/app", False),
    "gepnic_odisha": ("Odisha eTenders", "https://tendersodisha.gov.in/nicgep/app", False),
    "gepnic_goa": ("Goa eProcurement", "https://eprocure.goa.gov.in/nicgep/app", False),
    "gepnic_jharkhand": ("Jharkhand eTenders", "https://jharkhandtenders.gov.in/nicgep/app", False),
    "gepnic_rajasthan": ("Rajasthan eProcurement", "https://eproc.rajasthan.gov.in/nicgep/app", False),
    "gepnic_assam": ("Assam eTenders", "https://assamtenders.gov.in/nicgep/app", False),
    "gepnic_arunachal": ("Arunachal Pradesh eTenders", "https://arunachaltenders.gov.in/nicgep/app", False),
    "gepnic_manipur": ("Manipur eTenders", "https://manipurtenders.gov.in/nicgep/app", False),
    "gepnic_meghalaya": ("Meghalaya eTenders", "https://meghalayatenders.gov.in/nicgep/app", False),
    "gepnic_mizoram": ("Mizoram eTenders", "https://mizoramtenders.gov.in/nicgep/app", False),
    "gepnic_nagaland": ("Nagaland eTenders", "https://nagalandtenders.gov.in/nicgep/app", False),
    "gepnic_sikkim": ("Sikkim eTenders", "https://sikkimtender.gov.in/nicgep/app", False),
    "gepnic_tripura": ("Tripura eTenders", "https://tripuratenders.gov.in/nicgep/app", False),
    "gepnic_jammukashmir": ("Jammu & Kashmir eTenders", "https://jktenders.gov.in/nicgep/app", False),
    "gepnic_ladakh": ("Ladakh eTenders", "https://tenders.ladakh.gov.in/nicgep/app", False),
    "gepnic_chandigarh": ("Chandigarh eTenders", "https://etenders.chd.nic.in/nicgep/app", False),
    "gepnic_puducherry": ("Puducherry eTenders", "https://pudutenders.gov.in/nicgep/app", False),
    "gepnic_haryana": ("Haryana eTenders (NIC)", "https://etenders.hry.nic.in/nicgep/app", False),
    "gepnic_madhyapradesh": ("Madhya Pradesh eTenders", "https://mptenders.gov.in/nicgep/app", False),
}

GEPNIC_BLOCKER = ("Tender details are read automatically; downloading documents needs a CAPTCHA, so analysts "
                  "upload documents for shortlisted tenders.")

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
    *[
        {
            "code": code,
            "name": name,
            "connector": "gepnic_html",
            "base_url": url,
            "acquisition_method": "HTML",
            "enabled": enabled,
            # a full organisation sweep is ~1 request per organisation, so these run less often than CPPP
            "schedule_minutes": 120,
            "config": {"base_url": url, "detail_mode": "candidates", "max_details_per_run": 300},
            "blocker": GEPNIC_BLOCKER,
        }
        for code, (name, url, enabled) in GEPNIC_PORTALS.items()
    ],
    # Buyers' own tender pages (Phase 3). Each was checked for robots.txt permission and a readable
    # tender table; documents are linked directly (no CAPTCHA) and run through the document pipeline.
    {
        "code": "buyer_sbi",
        "name": "State Bank of India — procurement news",
        "connector": "buyer_page",
        "base_url": "https://sbi.bank.in/web/sbi-in-the-news/procurement-news",
        "acquisition_method": "HTML",
        "schedule_minutes": 180,
        "config": {
            "pages": [{"url": "https://sbi.bank.in/web/sbi-in-the-news/procurement-news",
                       "organization": "State Bank of India"}],
            "render": "http",
            "reference_regex": r"^(?P<ref>[A-Z0-9][^:]{3,80}):",
        },
        "blocker": None,
    },
    {
        "code": "buyer_cdac",
        "name": "C-DAC — tenders",
        "connector": "buyer_page",
        "base_url": "https://www.cdac.in/index.aspx?id=tenders",
        "acquisition_method": "HTML",
        "schedule_minutes": 360,
        "config": {
            "pages": [{"url": "https://www.cdac.in/index.aspx?id=tenders",
                       "organization": "Centre for Development of Advanced Computing (C-DAC)"}],
            "render": "http",
            "follow_detail": True,
        },
        "blocker": None,
    },
    {
        "code": "buyer_isro",
        "name": "ISRO — tenders",
        "connector": "buyer_page",
        "base_url": "https://www.isro.gov.in/Tenders.html",
        "acquisition_method": "HTML",
        "enabled": False,  # rows carry only an advert number, so every new notice PDF must be read
        "schedule_minutes": 720,
        "config": {
            "pages": [{"url": "https://www.isro.gov.in/Tenders.html",
                       "organization": "Indian Space Research Organisation (ISRO)"}],
            "render": "http",
            "screen": "documents",
            "max_details_per_run": 40,
        },
        "blocker": "Listing shows only advert numbers; tenders are screened by reading each notice PDF.",
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

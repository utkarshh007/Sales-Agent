"""Plain-language tender summaries, client contact details and how to submit."""
from __future__ import annotations

import pytest

from app.brief import compose
from app.contacts import extract

# layout as extracted from SBI's real EOI PDF: table cells come out one per line
SBI_SCHEDULE = """Schedule of Events
Sl
No
Particulars
Remarks
1
Contact details of issuing department
(Name, Designation, Mobile No., Email
and office address for sending any kind
of correspondence regarding this EOI)
Name: Bhavesh Neharu Nikam
Designation: Chief Manager
Email ID: bhavesh.nikam@sbi.co.in
Contact Address: State Bank Global IT
Centre, Ground Floor, A Wing, Sector
11, CBD Belapur, Navi Mumbai-400614
Contact Number: +91 9833784846
2
Bid Document Availability including
changes/amendments, if any to be
issued
7
Address for submission of Bids
(details of e-Procurement Agency portal
wherein online bid has to be submitted)
M/s E-Procurement Technologies Ltd,
Ahmedabad
Website: https://etender.sbi/SBI/
8
Date and Time of opening of Technical
12
Contact
details
of
e-Procurement
agency appointed for e-procurement
Executive - Client Service
Nithya
e-Procurement Technologies Limited
Email: nithya@eptl.in
Mobile: 7859800609
"""


def _brief(title, org="State Bank of India", kind="SERVICE", **kw):
    return compose(title=title, organization=org, opportunity_type=kind, capability=kw.get("capability"),
                   products=kw.get("products", []), extracted=kw.get("extracted"), closing=kw.get("closing"),
                   value=kw.get("value"))


@pytest.mark.parametrize("title, org, headline", [
    ("TENDER NOTICE FOR REQUEST FOR EXPRESSION OF INTEREST FOR PROCUREMENT OF TOOL FOR CONDUCTING TABLE-TOP EXERCISE (TTX)",
     "State Bank of India", "State Bank of India wants to buy a tool for conducting table-top exercise (TTX)."),
    ("Supply, installation and commissioning of SIEM solution with 3 years FMS for Security Operations Centre",
     "NHPC Limited", "NHPC Limited wants to buy a SIEM solution with 3 years FMS for Security Operations Centre."),
    ("EOI for Selection of partner for SITC of Cyber Security Infrastructure for CNS ATM Systems",
     "Telecommunications Consultants India Limited",
     "Telecommunications Consultants India Limited is looking for a partner to work with on Cyber Security Infrastructure for CNS ATM Systems."),
    ("Selection of CERT-In empanelled agency for comprehensive IS audit and VAPT of core banking applications",
     "Punjab & Sind Bank",
     "Punjab & Sind Bank wants an independent CERT-In empanelled agency for comprehensive IS audit and VAPT of core banking applications."),
    ("Setting up of Cyber Forensic Laboratory including forensic workstations", "Uttar Pradesh Police",
     "Uttar Pradesh Police wants to set up a Cyber Forensic Laboratory including forensic workstations."),
    ("Expression of Interest (EOI) for appointment of a CERT-In empaneled Third-Party Auditor (TPA)",
     "SECURITY PRINTING AND MINTING CORPORATION OF INDIA-SPMCIL",
     "SPMCIL needs a CERT-In empaneled Third-Party Auditor (TPA)."),
])
def test_plain_headline(title, org, headline):
    assert _brief(title, org)["headline"] == headline


def test_reference_only_titles_do_not_pretend_to_describe_the_work():
    h = _brief("NIT20AERAJKOT202627", "Central Public Works Department (CPWD)")["headline"]
    assert "only as “NIT20AERAJKOT202627”" in h and "tender document" in h


def test_brief_restates_known_facts_only():
    b = _brief("Empanelment of vendors for cybersecurity training", kind="SERVICE", capability="Security Awareness Training",
               extracted={"contract_duration": {"value": "3 years"}, "scope_of_work": {"value": "UNKNOWN"}},
               closing="28 Oct 2026, 3:00 pm IST")
    assert b["headline"].startswith("State Bank of India wants to build a panel of approved vendors for")
    assert b["kind"].startswith("It is a services job")
    labels = [p["label"] for p in b["points"]]
    assert labels == ["Contract period", "Bids close"], "UNKNOWN scope and missing value are left out, not invented"


# ------------------------------------------------------------------ contacts
def test_sbi_schedule_gives_officer_contact_portal_submission_and_helpdesk():
    c = extract(portal_code="buyer_sbi", portal_name="SBI", portal_url="https://sbi.co.in", organization="State Bank of India",
                department=None, portal_contact=None, texts=[("portal tender page", ""), ("NIT.pdf", SBI_SCHEDULE)])
    cl = c["client"]
    assert (cl["name"], cl["designation"], cl["email"], cl["phone"]) == (
        "Bhavesh Neharu Nikam", "Chief Manager", "bhavesh.nikam@sbi.co.in", "+91 9833784846")
    assert cl["address"] == "State Bank Global IT Centre, Ground Floor, A Wing, Sector 11, CBD Belapur, Navi Mumbai-400614"
    assert cl["source"] == "NIT.pdf"
    s = c["submission"]
    assert s["mode"] == "ONLINE_PORTAL" and s["url"] == "https://etender.sbi/SBI/"
    assert "email" not in s, "the officer's email is for queries; bids go through the portal"
    assert c["helpdesk"]["email"] == "nithya@eptl.in" and c["helpdesk"]["phone"] == "7859800609"
    assert c["other_emails"] == []


def test_email_submission_is_reported_only_when_the_text_says_so():
    text = ("Interested agencies may send queries to it.cell@buyer.gov.in. "
            "The proposals should be submitted by e-mail to tenders@buyer.gov.in on or before 20 October 2026.")
    c = extract(portal_code="buyer_x", portal_name="X", portal_url=None, organization="Buyer", department=None,
                portal_contact=None, texts=[("RFP.pdf", text)])
    assert c["submission"]["mode"] == "EMAIL" and c["submission"]["email"] == "tenders@buyer.gov.in"
    assert [o["email"] for o in c["other_emails"]] == ["it.cell@buyer.gov.in"]


def test_gepnic_tender_uses_inviting_authority_and_online_submission():
    c = extract(portal_code="gepnic_central", portal_name="Central eProcurement", portal_url="https://etenders.gov.in/eprocure/app",
                organization="TCIL", department=None, portal_contact="GM DCCS — TCIL BHAWAN GK - 1 , NEW DELHI - 110048",
                texts=[("portal tender page", "Title: x")])
    assert c["client"]["name"] == "GM DCCS" and c["client"]["address"].startswith("TCIL BHAWAN")
    assert c["submission"] == {**c["submission"], "mode": "ONLINE_PORTAL", "url": "https://etenders.gov.in/eprocure/app"}


def test_nothing_known_stays_unknown():
    c = extract(portal_code="manual", portal_name="Manual", portal_url="", organization="RBI", department=None,
                portal_contact=None, texts=[("portal tender page", "")])
    assert c["submission"] == {"mode": "UNKNOWN"} and c["helpdesk"] is None
    assert c["client"] == {"organization": "RBI", "department": None}


def test_physical_submission_and_portal_helpdesk_emails_ignored():
    text = ("The bid shall be submitted in a sealed envelope at the address below.\n"
            "For portal issues write to support-eproc@nic.in")
    c = extract(portal_code="buyer_x", portal_name="X", portal_url=None, organization="B", department=None,
                portal_contact=None, texts=[("NIT.pdf", text)])
    assert c["submission"]["mode"] == "PHYSICAL"
    assert c["other_emails"] == [], "the national portal helpdesk is not the buyer"


def test_api_lists_and_details_carry_the_summary_and_contacts(tmp_path, settings, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.models import Portal, Tender, TenderDocument, User
    from app.security import hash_password, limiter
    from tests.helpers import make_db
    Session = make_db(tmp_path)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
    with Session() as s:
        s.add(User(email="v@x.io", password_hash=hash_password("Correct-Horse-9-Battery"), role="viewer"))
        portal = s.scalar(select(Portal).where(Portal.code == "manual"))
        t = Tender(portal_id=portal.id, fingerprint="fp", content_hash="h", decision="ACCEPTED", opportunity_type="OEM",
                   title="Procurement of tool for conducting table-top exercise (TTX)", organization="State Bank of India")
        s.add(t)
        s.flush()
        s.add(TenderDocument(tender_id=t.id, filename="NIT.pdf", status="EXTRACTED", extracted_text=SBI_SCHEDULE,
                             sha256="0" * 64, size_bytes=1, storage_path="x"))
        s.commit()
        tid = t.id
    from app.api.main import create_app
    limiter._hits.clear()
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"email": "v@x.io", "password": "Correct-Horse-9-Battery"})
    row = c.get("/api/tenders", params={"decision": "ACCEPTED"}).json()["items"][0]
    assert row["plain_summary"] == "State Bank of India wants to buy a tool for conducting table-top exercise (TTX)."
    d = c.get(f"/api/tenders/{tid}").json()
    assert d["brief"]["kind"].startswith("It is mainly a product purchase")
    assert d["contacts"]["client"]["email"] == "bhavesh.nikam@sbi.co.in"
    assert d["contacts"]["submission"]["url"] == "https://etender.sbi/SBI/"

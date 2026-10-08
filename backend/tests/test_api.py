from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import ManualReview, Tender, User
from app.security import hash_password
from tests.helpers import fixture_connector, make_db

PW = "Correct-Horse-9-Battery"


@pytest.fixture
def client(tmp_path, settings, monkeypatch):
    Session = make_db(tmp_path)
    settings.DOCUMENT_STORAGE_DIR = str(tmp_path / "docs")
    monkeypatch.setattr("app.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
    with Session() as s:
        for email, role in (("admin@x.io", "admin"), ("analyst@x.io", "analyst"), ("viewer@x.io", "viewer")):
            s.add(User(email=email, password_hash=hash_password(PW), role=role))
        s.commit()
        from app.models import Portal
        from app.pipeline import discover_portal
        from app.worker import Worker
        connector, _ = fixture_connector(settings)
        discover_portal(s, s.scalar(select(Portal).where(Portal.code == "cppp")), settings, connector)
    Worker(settings).drain()
    from app.api.main import create_app
    from app.security import limiter
    limiter._hits.clear()
    return TestClient(create_app()), Session


def login(c: TestClient, email: str) -> str:
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c.cookies.get("ti_csrf")


def test_requires_authentication(client):
    c, _ = client
    assert c.get("/api/tenders").status_code == 401


def test_wrong_password_rejected_and_rate_limited(client):
    c, _ = client
    codes = [c.post("/api/auth/login", json={"email": "viewer@x.io", "password": "nope"}).status_code for _ in range(12)]
    assert codes[0] == 401 and 429 in codes


def test_list_and_detail_with_explanations(client):
    c, _ = client
    login(c, "viewer@x.io")
    r = c.get("/api/tenders", params={"decision": "LIVE"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["opportunity_type"] == "OEM" and items[0]["score"] >= items[-1]["score"]
    d = c.get(f"/api/tenders/{items[0]['id']}").json()
    assert d["matches"][0]["products"][0]["name"] == "Splunk"
    assert d["score_breakdown"]["oem"]["reason"]
    assert d["audit"][0]["action"] in ("DECISION", "TENDER_NEW")
    ov = c.get("/api/overview").json()
    assert ov["rejected"] == 12 and ov["manual_review"] == 1 and ov["analysis_mode"] == "RULES_ONLY"


def test_mutations_require_csrf_and_role(client):
    c, Session = client
    with Session() as s:
        tid = s.scalar(select(Tender.id).where(Tender.portal_tender_id == "2026_BANK_2002"))
    login(c, "viewer@x.io")
    csrf = c.cookies.get("ti_csrf")
    assert c.post(f"/api/tenders/{tid}/reanalyze", headers={"X-CSRF-Token": csrf}).status_code == 403  # viewer
    c.post("/api/auth/logout")
    csrf = login(c, "analyst@x.io")
    assert c.post(f"/api/tenders/{tid}/reanalyze").status_code == 403  # missing CSRF
    assert c.post(f"/api/tenders/{tid}/reanalyze", headers={"X-CSRF-Token": csrf}).status_code == 202


def test_upload_and_review_resolution(client):
    c, Session = client
    csrf = login(c, "analyst@x.io")
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_BANK_2002"))
        review_id = s.scalar(select(ManualReview.id).where(ManualReview.tender_id == t.id, ManualReview.status == "OPEN"))
    r = c.post(f"/api/tenders/{t.id}/documents", headers={"X-CSRF-Token": csrf},
               files={"file": ("scope.txt", b"Scope of work: red team assessment. Estimated cost Rs. 18,00,000", "text/plain")})
    assert r.status_code == 201
    r = c.post(f"/api/reviews/{review_id}/resolve", headers={"X-CSRF-Token": csrf},
               json={"resolution": "ACCEPTED", "notes": "value confirmed by phone with department"})
    assert r.status_code == 200 and r.json()["tender_decision"] == "ACCEPTED"
    detail = c.get(f"/api/tenders/{t.id}").json()
    assert any(a["action"] == "MANUAL_REVIEW_RESOLVED" for a in detail["audit"])
    assert detail["documents"][0]["filename"] == "scope.txt"


def test_manual_tender_entry(client):
    c, _ = client
    csrf = login(c, "analyst@x.io")
    r = c.post("/api/tenders", headers={"X-CSRF-Token": csrf},
               json={"title": "Procurement of Data Loss Prevention solution", "organization": "IRDAI",
                     "closing_at": "2026-12-01T10:00:00Z", "tender_value_inr": 30000000})
    assert r.status_code == 201
    assert c.post("/api/tenders", headers={"X-CSRF-Token": csrf},
                  json={"title": "Procurement of Data Loss Prevention solution", "organization": "IRDAI"}).status_code == 409


def test_settings_endpoint_hides_secrets(client):
    c, _ = client
    login(c, "viewer@x.io")
    cfg = c.get("/api/settings").json()
    assert cfg["SERVICE_MAX_VALUE_INR"] == 3_000_000
    assert "SECRET_KEY" not in cfg and "ANTHROPIC_API_KEY" not in cfg and "SMTP_PASSWORD" not in cfg

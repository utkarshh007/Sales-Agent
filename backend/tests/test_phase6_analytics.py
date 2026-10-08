"""Phase 6 — bid outcomes, historical intelligence, analytics."""
from __future__ import annotations

import re
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.catalog import get_catalog
from app.db import utcnow
from app.history import embed_tender, recurrence, similar_tenders, subject_of
from app.models import BidOutcome, ManualReview, Portal, RejectionReason, Tender, TenderSource, User
from app.security import hash_password, limiter
from app.semantic import SemanticMatcher, set_semantic_matcher
from tests.helpers import make_db

PW = "Correct-Horse-9-Battery"


# ------------------------------------------------------------------ subject extraction
@pytest.mark.parametrize("title, subject", [
    ("EOI Document for Selection of partner for Establishment of Next Generation Security Operation Centre (NGSOC)",
     "Establishment of Next Generation Security Operation Centre (NGSOC)"),
    ("TENDER NOTICE FOR EMPANELMENT OF VENDORS FOR CYBERSECURITY TRAINING", "CYBERSECURITY TRAINING"),
    ("Supply, installation and commissioning of SIEM solution with 3 years FMS", "SIEM solution with 3 years FMS"),
    ("EOI Document for Selection of Consortium Partner for participation in BSNL Tender for supply of servers", "servers"),
    ("Supply Installation and commissioning of CCTV System at various locations in BBMB Colony at Slapper", "CCTV System"),
    ("SERVERS", "SERVERS"),
])
def test_subject_strips_boilerplate_and_location(title, subject):
    assert subject_of(title) == subject


# ------------------------------------------------------------------ history with a transparent fake embedder
WORDS = ["cctv", "camera", "surveillance", "siem", "log", "audit", "road", "perimeter", "widening", "security"]


def fake_embed(texts):
    return np.array([[float(w in t.lower()) for w in WORDS] + [0.3] for t in texts])


@pytest.fixture
def db(tmp_path, settings):
    Session = make_db(tmp_path)
    m = SemanticMatcher(get_catalog(), settings, embed=fake_embed)
    set_semantic_matcher(m)
    yield Session, m
    set_semantic_matcher(None)


def _tender(s, title, org="Org A", days_ago=1, decision="REJECTED", **kw):
    portal = s.scalar(select(Portal).where(Portal.code == "manual"))
    t = Tender(portal_id=portal.id, fingerprint=f"fp-{title}-{days_ago}", content_hash="x", title=title,
               organization=org, decision=decision, published_at=utcnow() - timedelta(days=days_ago),
               first_seen_at=utcnow() - timedelta(days=min(days_ago, 30)), **kw)
    s.add(t)
    s.flush()
    return t


def test_similar_tenders_need_meaning_and_distinctive_words(db):
    Session, m = db
    with Session() as s:
        a = _tender(s, "Supply of CCTV camera surveillance system at Delhi")
        b = _tender(s, "SITC of CCTV camera surveillance at Pune office")
        c = _tender(s, "Widening of perimeter road at airport")
        d = _tender(s, "Supply of security guards at Delhi")  # shares the place, not the subject
        for t in (a, b, c, d):
            embed_tender(s, t, m)
        s.commit()
        got = [x["id"] for x in similar_tenders(s, a)]
        assert got == [b.id]


def test_shared_generic_words_without_a_shared_phrase_are_not_similar(tmp_path, settings):
    """With meaning taken out (every embedding identical), only the wording rule decides."""
    Session = make_db(tmp_path)
    m = SemanticMatcher(get_catalog(), settings, embed=lambda texts: np.ones((len(texts), 4)))
    with Session() as s:
        soc = _tender(s, "Supply of SIEM solution for Security Operations Centre")
        ngsoc = _tender(s, "Establishment of Next Generation Security Operation Center")
        dc = _tender(s, "Construction and Operations of Data Centre on DBFOT model")
        for t in (soc, ngsoc, dc):
            embed_tender(s, t, m)
        s.commit()
        assert [x["id"] for x in similar_tenders(s, soc)] == [ngsoc.id]


def test_recurring_tender_from_same_buyer_is_detected(db):
    Session, m = db
    with Session() as s:
        old = _tender(s, "Annual comprehensive security audit of IT systems", org="City Bank", days_ago=370)
        new = _tender(s, "Annual comprehensive security audit of IT systems 2026-27", org="City Bank", days_ago=5)
        other_org = _tender(s, "Annual comprehensive security audit of IT systems", org="Other Bank", days_ago=360)
        for t in (old, new, other_org):
            embed_tender(s, t, m)
        s.commit()
        r = recurrence(s, new)
        assert r["previous_id"] == old.id and 360 <= r["interval_days"] <= 370
        assert r["next_expected_around"] > utcnow().date().isoformat()


# ------------------------------------------------------------------ analytics
def test_analytics_funnel_sources_reasons_and_outcomes(db):
    from app.analytics import overview
    Session, _ = db
    with Session() as s:
        portal = s.scalar(select(Portal).where(Portal.code == "cppp"))
        made = []
        for i in range(20):
            decision = "ACCEPTED" if i < 4 else "REJECTED"
            t = _tender(s, f"Tender number {i}", decision=decision, primary_capability="SIEM / Security Analytics" if i < 4 else None,
                        opportunity_type="OEM" if i < 4 else "UNRELATED", analysis_mode="RULES_ONLY" if i < 4 else "PREFILTER")
            s.add(TenderSource(tender_id=t.id, portal_id=portal.id, fingerprint=f"src{i}", listing_hash="h"))
            if i >= 4:
                s.add(RejectionReason(tender_id=t.id, tender_version=1, stage="PREFILTER", code="UNRELATED", detail="x"))
            made.append(t)
        s.add(BidOutcome(tender_id=made[0].id, stage="WON", award_value_inr=1_000_000))
        s.add(BidOutcome(tender_id=made[1].id, stage="LOST", winner="Acme Cyber", loss_reason="price",
                         our_bid_value_inr=1_100_000, award_value_inr=1_000_000))
        s.add(BidOutcome(tender_id=made[2].id, stage="NO_BID", no_bid_reason="not_eligible"))
        s.commit()
        o = overview(s, 90)
    f = o["funnel"]
    assert (f["discovered"], f["surfaced"], f["pursued"], f["won"]) == (20, 4, 2, 1)
    assert f["win_rate"] == 0.5 and f["win_rate_sample"] == 2
    cppp = next(x for x in o["sources"] if x["code"] == "cppp")
    assert cppp["seen"] == 20 and cppp["surfaced"] == 4 and cppp["tenders_per_relevant"] == 5
    assert o["screening"]["decided_without_llm"] == 16 and o["screening"]["rejection_reasons"][0]["code"] == "UNRELATED"
    out = o["outcomes"]
    assert out["competitors"] == [{"name": "Acme Cyber", "wins_against_us": 1}]
    assert out["no_bid_reasons"] == [{"reason": "not_eligible", "count": 1}]
    assert out["median_price_gap_when_lost"] == 0.1  # we bid 10% above the winning price
    assert out["enough_data"] is False, "2 decided bids is not enough to read win rates into"


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path, settings, monkeypatch):
    Session = make_db(tmp_path)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
    with Session() as s:
        s.add(User(email="analyst@x.io", password_hash=hash_password(PW), role="analyst"))
        s.add(User(email="viewer@x.io", password_hash=hash_password(PW), role="viewer"))
        t = _tender(s, "Procurement of SIEM solution", decision="MANUAL_REVIEW", closing_at=utcnow() + timedelta(days=10))
        s.add(ManualReview(tender_id=t.id, tender_version=1, reason_code="HYBRID_REVIEW_REQUIRED", reason="split unknown"))
        s.commit()
        tid = t.id
    from app.api.main import create_app
    limiter._hits.clear()
    return TestClient(create_app()), Session, tid


def _login(c, email):
    c.post("/api/auth/login", json={"email": email, "password": PW})
    return c.cookies.get("ti_csrf")


def test_outcome_api_rules_and_audit(client):
    c, Session, tid = client
    csrf = _login(c, "viewer@x.io")
    assert c.put(f"/api/tenders/{tid}/outcome", json={"stage": "BIDDING"}, headers={"X-CSRF-Token": csrf}).status_code == 403
    c.post("/api/auth/logout")
    csrf = _login(c, "analyst@x.io")
    h = {"X-CSRF-Token": csrf}
    assert c.put(f"/api/tenders/{tid}/outcome", json={"stage": "NO_BID"}, headers=h).status_code == 400
    assert c.put(f"/api/tenders/{tid}/outcome", json={"stage": "MAYBE"}, headers=h).status_code == 422
    r = c.put(f"/api/tenders/{tid}/outcome", json={"stage": "LOST", "winner": "Acme Cyber", "loss_reason": "price",
                                                   "award_value_inr": 2_000_000}, headers=h)
    assert r.status_code == 200 and r.json()["outcome"]["winner"] == "Acme Cyber"
    assert c.get(f"/api/tenders/{tid}/outcome").json()["outcome"]["stage"] == "LOST"
    audit = c.get(f"/api/tenders/{tid}").json()["audit"]
    assert any(a["action"] == "BID_OUTCOME_UPDATED" and a["decision"] == "LOST" for a in audit)


def test_pursuing_from_review_starts_the_bid_record_and_pipeline(client):
    c, Session, tid = client
    csrf = _login(c, "analyst@x.io")
    with Session() as s:
        rid = s.scalar(select(ManualReview.id).where(ManualReview.tender_id == tid))
    c.post(f"/api/reviews/{rid}/resolve", json={"resolution": "ACCEPTED", "notes": "split confirmed"},
           headers={"X-CSRF-Token": csrf})
    assert c.get(f"/api/tenders/{tid}/outcome").json()["outcome"]["stage"] == "CONSIDERING"
    pipe = c.get("/api/pipeline").json()
    assert [r["id"] for r in pipe["CONSIDERING"]] == [tid] and pipe["NOT_STARTED"] == []


def test_history_and_analytics_endpoints(client):
    c, _, tid = client
    _login(c, "viewer@x.io")
    h = c.get(f"/api/tenders/{tid}/history").json()
    assert set(h) == {"similar", "buyer", "recurrence"} and h["buyer"]["organization"] == "Org A"
    a = c.get("/api/analytics", params={"days": 30}).json()
    assert a["funnel"]["discovered"] == 1 and a["window_days"] == 30

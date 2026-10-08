"""Executive dashboard: filters scope every number, KPIs and growth, charts' data, insights."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.dashboard import build
from app.db import utcnow
from app.models import BidOutcome, ManualReview, Portal, RejectionReason, Tender, User
from app.security import hash_password, limiter
from tests.helpers import make_db

NOW = utcnow()


def _t(s, portal, n, *, decision="REJECTED", type_="UNRELATED", seen_days_ago=5, closes_in=20, value=None, score=None,
       cap=None, segment=None, priority=None, mode="PREFILTER"):
    t = Tender(portal_id=portal.id, fingerprint=f"fp{portal.code}{n}", content_hash="h", title=f"Tender {portal.code} {n}",
               organization="Org", decision=decision, opportunity_type=type_, first_seen_at=NOW - timedelta(days=seen_days_ago),
               closing_at=NOW + timedelta(days=closes_in), tender_value_inr=value, score=score, primary_capability=cap,
               priority=priority, analysis_mode=mode, extracted={"_segment": {"name": segment}} if segment else {})
    s.add(t)
    s.flush()
    return t


@pytest.fixture
def Session(tmp_path):
    Session = make_db(tmp_path)
    with Session() as s:
        cppp = s.scalar(select(Portal).where(Portal.code == "cppp"))
        manual = s.scalar(select(Portal).where(Portal.code == "manual"))
        for i in range(200):  # noise: read and rejected by rules
            t = _t(s, cppp, i)
            s.add(RejectionReason(tender_id=t.id, tender_version=1, stage="PREFILTER", code="UNRELATED", detail="x"))
        for i in range(40):  # the previous 30-day period: fewer tenders read
            _t(s, cppp, 1000 + i, seen_days_ago=45)
        _t(s, cppp, 2000, seen_days_ago=62)  # history reaches back past the previous period, so growth is comparable
        siem = _t(s, manual, 1, decision="ACCEPTED", type_="OEM", value=50_000_000, score=82, cap="SIEM", segment="Bank",
                  priority="HOT", closes_in=3, mode="RULES_ONLY")
        _t(s, manual, 2, decision="ACCEPTED", type_="SERVICE", value=1_800_000, score=70, cap="SIEM", segment="Bank",
           closes_in=10, mode="RULES_ONLY")
        _t(s, manual, 3, decision="MANUAL_REVIEW", type_="HYBRID", score=55, cap="VAPT", segment="Regulator",
           closes_in=25, mode="RULES_ONLY")
        won = _t(s, manual, 4, decision="ACCEPTED", type_="SERVICE", value=2_500_000, score=75, cap="SIEM", segment="Bank",
                 closes_in=-5, mode="RULES_ONLY")  # closed, and won
        s.add(BidOutcome(tender_id=won.id, stage="WON", award_value_inr=2_400_000))
        s.add(ManualReview(tender_id=siem.id, tender_version=1, reason_code="X", reason="y",
                           created_at=NOW - timedelta(hours=60)))
        s.commit()
    return Session


def test_growth_needs_history_covering_the_previous_period(tmp_path):
    Session = make_db(tmp_path)
    with Session() as s:
        cppp = s.scalar(select(Portal).where(Portal.code == "cppp"))
        _t(s, cppp, 1, seen_days_ago=45)
        _t(s, cppp, 2, seen_days_ago=5)
        s.commit()
        d = build(s, days=30, now=NOW)
    assert d["window"]["has_previous"] is False and d["kpis"][0]["delta"] is None


def test_kpis_funnel_and_growth(Session):
    with Session() as s:
        d = build(s, days=30, now=NOW)
    k = {x["key"]: x for x in d["kpis"]}
    assert k["read"]["value"] == 204 and k["read"]["prev"] == 40 and k["read"]["delta"] == pytest.approx(4.1)
    assert k["surfaced"]["value"] == 4
    assert k["open_value"]["value"] == 51_800_000, "open, surfaced, stated values only (the won one has closed)"
    assert k["closing_7d"]["value"] == 1
    assert k["avg_score"]["value"] == pytest.approx((82 + 70 + 55 + 75) / 4, abs=0.1)
    assert k["win_rate"]["value"] == 1.0 and "too few" in k["win_rate"]["note"]
    assert [f["count"] for f in d["funnel"]] == [204, 4, 4, 1, 1, 1]


def test_filters_scope_every_number(Session):
    with Session() as s:
        d = build(s, days=30, type="SERVICE", now=NOW)
    k = {x["key"]: x for x in d["kpis"]}
    assert k["read"]["value"] == 2 and k["surfaced"]["value"] == 2
    assert [m["count"] for m in d["type_mix"]] == [2, 0, 0]
    assert d["filters"]["applied"] == {"type": "SERVICE"}
    with Session() as s:
        seg = build(s, days=30, segment="Regulator", now=NOW)
    assert [c["label"] for c in seg["by_capability"]] == ["VAPT"]


def test_chart_data(Session):
    with Session() as s:
        d = build(s, days=30, now=NOW)
    assert d["by_capability"][0] == {"label": "SIEM", "count": 3, "value": 54_300_000, "avg_score": 75.7}
    assert sum(b["count"] for b in d["value_bands"]) == 4, "every surfaced tender lands in exactly one band"
    assert [b["count"] for b in d["deadlines"]] == [1, 1, 1, 0]
    assert d["rejections"][0]["code"] == "UNRELATED" and d["rejections"][0]["count"] == 200
    cppp = next(x for x in d["sources"] if x["code"] == "cppp")
    assert cppp["read"] == 200 and cppp["surfaced"] == 0
    assert [t["score"] for t in d["top"]] == [82, 70, 55], "open only, best first"
    assert d["pipeline"][0] == {"stage": "NOT_STARTED", "count": 3}


def test_insights_are_computed_from_the_page_numbers(Session):
    with Session() as s:
        s.get(Portal, s.scalar(select(Portal.id).where(Portal.code == "cppp"))).last_status = "ERROR"
        s.commit()
        d = build(s, days=30, now=NOW)
    titles = [i["title"] for i in d["insights"]]
    assert titles[0] == "1 open opportunity closes within 7 days"
    assert d["insights"][0]["kind"] == "risk" and len(d["insights"][0]["tender_ids"]) == 1
    assert "1 HOT opportunity not yet picked up" in titles
    review = next(i for i in d["insights"] if "review queue" in i["title"])
    assert review["kind"] == "risk", "60 hours is past the 2-day mark"
    assert any(i["kind"] == "anomaly" and "Central Public Procurement Portal" in i["title"] for i in d["insights"])
    assert any(i["kind"] == "trend" and "Tenders read up" in i["title"] for i in d["insights"])
    assert "SIEM leads demand" in titles


def test_endpoint(tmp_path, settings, monkeypatch):
    Session = make_db(tmp_path)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.main.get_settings", lambda: settings)
    with Session() as s:
        s.add(User(email="v@x.io", password_hash=hash_password("Correct-Horse-9-Battery"), role="viewer"))
        s.commit()
    from app.api.main import create_app
    limiter._hits.clear()
    c = TestClient(create_app())
    assert c.get("/api/dashboard").status_code == 401
    c.post("/api/auth/login", json={"email": "v@x.io", "password": "Correct-Horse-9-Battery"})
    r = c.get("/api/dashboard", params={"days": 0})
    assert r.status_code == 200 and r.json()["kpis"][0]["value"] == 0
    assert c.get("/api/dashboard", params={"type": "NOPE"}).status_code == 422

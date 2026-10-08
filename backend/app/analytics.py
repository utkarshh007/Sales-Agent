"""Analytics (Phase 6): funnel, source yield, screening, opportunity mix, trends, review turnaround,
and bid outcomes. Every metric carries its sample size so the dashboard can say when there is not yet
enough history to read anything into it."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import as_utc, utcnow
from app.models import BidOutcome, ManualReview, Portal, RejectionReason, Tender, TenderSource

SURFACED = ("ACCEPTED", "MANUAL_REVIEW")
PURSUED_STAGES = ("BIDDING", "SUBMITTED", "WON", "LOST")
REJECTION_LABELS = {
    "UNRELATED": "No cyber requirement (title, category or documents)",
    "CLOSED": "Already closed when found",
    "SERVICE_OVER_CAP": "Service tender above ₹30 lakh",
    "NO_CAPABILITY_MATCH": "Requirements don't map to the catalog",
    "GENERIC_SECURITY_ONLY": "Generic security wording only",
    "BELOW_MIN_SCORE": "Score below the minimum",
    "OEM_OVER_LIMIT": "Above the OEM value limit",
    "ADJACENT_ONLY": "Only an adjacent match (suppressed)",
    "HYBRID_SERVICE_COMPONENT_OVER_CAP": "Hybrid with service part above ₹30 lakh",
}
MIN_FOR_RATES = 10  # fewer data points than this and a rate is shown as "not enough data"


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def overview(session: Session, days: int = 90, now: datetime | None = None) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    tenders = session.scalars(select(Tender).where(Tender.first_seen_at >= since)).all()
    ids = [t.id for t in tenders]
    outcomes = {o.tender_id: o for o in session.scalars(select(BidOutcome).where(BidOutcome.tender_id.in_(ids)))} if ids else {}
    surfaced = [t for t in tenders if t.decision in SURFACED]
    stage = Counter(o.stage for o in outcomes.values())
    pursued = sum(stage[s] for s in PURSUED_STAGES)
    decided = stage["WON"] + stage["LOST"]

    first = min((as_utc(t.first_seen_at) for t in tenders), default=None)
    history_days = round((now - first).total_seconds() / 86400, 1) if first else 0.0

    return {
        "window_days": days,
        "history_days": history_days,
        "funnel": {
            "discovered": len(tenders), "surfaced": len(surfaced),
            "pursued": pursued, "submitted": stage["SUBMITTED"] + decided, "won": stage["WON"],
            "surfaced_rate": _rate(len(surfaced), len(tenders)),
            "win_rate": _rate(stage["WON"], decided) if decided >= 1 else None,
            "win_rate_sample": decided,
        },
        "sources": _sources(session, since),
        "screening": _screening(session, tenders, since),
        "mix": _mix(surfaced),
        "trend": _trend(tenders, now),
        "reviews": _reviews(session, since),
        "outcomes": _outcomes(outcomes, {t.id: t for t in tenders}),
        "pipeline_value": _pipeline_value(surfaced, outcomes, now),
    }


def _sources(session: Session, since: datetime) -> list[dict]:
    rows = session.execute(
        select(Portal.code, Portal.name, Portal.enabled, TenderSource.detail_hash, Tender.decision)
        .join(TenderSource, TenderSource.portal_id == Portal.id)
        .join(Tender, Tender.id == TenderSource.tender_id)
        .where(TenderSource.first_seen_at >= since)).all()
    agg: dict[str, dict] = {}
    for code, name, enabled, detail_hash, decision in rows:
        a = agg.setdefault(code, {"code": code, "name": name, "enabled": enabled, "seen": 0, "surfaced": 0, "detailed": 0})
        a["seen"] += 1
        a["surfaced"] += decision in SURFACED
        a["detailed"] += detail_hash is not None
    out = sorted(agg.values(), key=lambda a: (-a["surfaced"], -a["seen"]))
    for a in out:
        a["yield"] = _rate(a["surfaced"], a["seen"])
        a["tenders_per_relevant"] = round(a["seen"] / a["surfaced"]) if a["surfaced"] else None
    return out


def _screening(session: Session, tenders: list[Tender], since: datetime) -> dict:
    modes = Counter(t.analysis_mode or "PENDING" for t in tenders)
    latest: dict[int, str] = {}
    for tid, code, ver in session.execute(
            select(RejectionReason.tender_id, RejectionReason.code, RejectionReason.tender_version)
            .where(RejectionReason.created_at >= since).order_by(RejectionReason.tender_version)):
        latest[tid] = code  # the most recent version's reason wins
    reasons = Counter(latest.values())
    total = len(tenders) or 1
    deterministic = modes["PREFILTER"]
    return {
        "decided_without_llm": deterministic,
        "decided_without_llm_rate": _rate(deterministic, len(tenders)),
        "analysed_by_llm": modes["LLM"],
        "analysed_by_rules": modes["RULES_ONLY"],
        "rejection_reasons": [{"code": c, "label": REJECTION_LABELS.get(c, c.replace("_", " ").lower()), "count": n,
                               "share": round(n / total, 3)} for c, n in reasons.most_common()],
    }


def _mix(surfaced: list[Tender]) -> dict:
    def count(key) -> list[dict]:
        c = Counter(key(t) or "Unclassified" for t in surfaced)
        return [{"label": k, "count": v} for k, v in c.most_common(12)]
    return {
        "by_capability": count(lambda t: t.primary_capability),
        "by_type": count(lambda t: {"SERVICE": "Service", "OEM": "OEM / product", "HYBRID": "Hybrid"}.get(t.opportunity_type)),
        "by_segment": count(lambda t: ((t.extracted or {}).get("_segment") or {}).get("name")),
        "by_priority": count(lambda t: t.priority),
    }


def _trend(tenders: list[Tender], now: datetime, weeks: int = 12) -> list[dict]:
    start = (now - timedelta(weeks=weeks - 1)).date()
    start -= timedelta(days=start.weekday())  # Monday
    buckets: dict[str, dict] = {}
    for i in range(weeks):
        d = start + timedelta(weeks=i)
        buckets[d.isoformat()] = {"week": d.isoformat(), "discovered": 0, "surfaced": 0}
    for t in tenders:
        d = as_utc(t.first_seen_at).date()
        key = (d - timedelta(days=d.weekday())).isoformat()
        if key in buckets:
            buckets[key]["discovered"] += 1
            buckets[key]["surfaced"] += t.decision in SURFACED
    return list(buckets.values())


def _reviews(session: Session, since: datetime) -> dict:
    rows = session.scalars(select(ManualReview).where(ManualReview.created_at >= since)).all()
    resolved = [r for r in rows if r.status == "RESOLVED" and r.resolved_at]
    hours = [(as_utc(r.resolved_at) - as_utc(r.created_at)).total_seconds() / 3600 for r in resolved]
    return {
        "open": sum(r.status == "OPEN" for r in rows),
        "resolved": len(resolved),
        "pursued_after_review": sum(r.resolution == "ACCEPTED" for r in resolved),
        "median_hours_to_decide": round(statistics.median(hours), 1) if hours else None,
        "by_reason": [{"code": c, "count": n} for c, n in Counter(r.reason_code for r in rows if r.status == "OPEN").most_common()],
    }


def _outcomes(outcomes: dict[int, BidOutcome], tenders: dict[int, Tender]) -> dict:
    decided = [o for o in outcomes.values() if o.stage in ("WON", "LOST")]
    by: dict[str, dict[str, Counter]] = {"capability": defaultdict(Counter), "segment": defaultdict(Counter), "type": defaultdict(Counter)}
    for o in decided:
        t = tenders.get(o.tender_id)
        if t is None:
            continue
        keys = {"capability": t.primary_capability or "Unclassified",
                "segment": ((t.extracted or {}).get("_segment") or {}).get("name") or "Other", "type": t.opportunity_type}
        for dim, k in keys.items():
            by[dim][k][o.stage] += 1

    def table(dim: str) -> list[dict]:
        return [{"label": k, "won": c["WON"], "lost": c["LOST"], "win_rate": _rate(c["WON"], c["WON"] + c["LOST"])}
                for k, c in sorted(by[dim].items(), key=lambda kv: -(kv[1]["WON"] + kv[1]["LOST"]))]

    winners = Counter(o.winner.strip() for o in outcomes.values() if o.stage == "LOST" and o.winner)
    no_bid = Counter(o.no_bid_reason or "unspecified" for o in outcomes.values() if o.stage == "NO_BID")
    loss = Counter(o.loss_reason or "unspecified" for o in outcomes.values() if o.stage == "LOST")
    gaps = [(o.our_bid_value_inr - o.award_value_inr) / o.award_value_inr for o in decided
            if o.stage == "LOST" and o.our_bid_value_inr and o.award_value_inr]
    return {
        "decided": len(decided),
        "enough_data": len(decided) >= MIN_FOR_RATES,
        "win_rate_by_capability": table("capability"),
        "win_rate_by_segment": table("segment"),
        "win_rate_by_type": table("type"),
        "competitors": [{"name": n, "wins_against_us": c} for n, c in winners.most_common(10)],
        "no_bid_reasons": [{"reason": r, "count": c} for r, c in no_bid.most_common()],
        "loss_reasons": [{"reason": r, "count": c} for r, c in loss.most_common()],
        "median_price_gap_when_lost": round(statistics.median(gaps), 3) if gaps else None,
    }


def _pipeline_value(surfaced: list[Tender], outcomes: dict[int, BidOutcome], now: datetime) -> dict:
    open_ = [t for t in surfaced if as_utc(t.closing_at) is None or as_utc(t.closing_at) >= now]
    known = [(t, (t.value_analysis or {}).get("total_value_inr")) for t in open_]
    by_type: dict[str, int] = defaultdict(int)
    for t, v in known:
        if v:
            by_type[t.opportunity_type] += v
    return {
        "open_opportunities": len(open_),
        "with_stated_value": sum(1 for _, v in known if v),
        "stated_value_inr": sum(v for _, v in known if v),
        "stated_value_by_type": dict(by_type),
        "bidding_now": sum(1 for t in open_ if outcomes.get(t.id) and outcomes[t.id].stage in ("BIDDING", "SUBMITTED")),
    }

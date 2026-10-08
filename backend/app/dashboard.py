"""Executive dashboard: one filtered slice of the tender data, every KPI, chart and insight computed from it.

Filters (date window, source, opportunity type, buyer segment, capability) scope everything, so the numbers on
the page always agree. KPIs carry the previous period of equal length for growth, and insights are plain
statements computed from the same numbers, each one traceable to a chart or table on the page.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import PURSUED_STAGES, REJECTION_LABELS, SURFACED
from app.db import as_utc, utcnow
from app.models import BidOutcome, ManualReview, Portal, RejectionReason, Tender

TYPE_LABEL = {"SERVICE": "Service", "OEM": "OEM / product", "HYBRID": "Hybrid"}
CAP_INR = 3_000_000  # the ₹30 lakh service rule
VALUE_BANDS = [("Up to ₹30 L", 0, CAP_INR), ("₹30 L – 1 Cr", CAP_INR, 10_000_000), ("₹1 – 10 Cr", 10_000_000, 100_000_000),
               ("Above ₹10 Cr", 100_000_000, None)]
DEADLINE_BANDS = [("Within 7 days", 0, 7), ("8–14 days", 7, 14), ("15–30 days", 14, 30), ("Over 30 days", 30, None)]


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _segment(extracted: dict | None) -> str | None:
    return ((extracted or {}).get("_segment") or {}).get("name")


def _value(t) -> int | None:
    return (t.value_analysis or {}).get("total_value_inr") or t.tender_value_inr


class _Row:
    __slots__ = ("id", "title", "organization", "portal_id", "decision", "opportunity_type", "primary_capability",
                 "priority", "score", "closing_at", "first_seen_at", "tender_value_inr", "value_analysis", "segment",
                 "analysis_mode", "matched_products", "extracted")

    def __init__(self, r) -> None:
        for k in self.__slots__:
            if k != "segment":
                setattr(self, k, getattr(r, k))
        self.closing_at, self.first_seen_at = as_utc(r.closing_at), as_utc(r.first_seen_at)
        self.segment = _segment(r.extracted)


def _load(session: Session) -> list[_Row]:
    cols = [getattr(Tender, k) for k in _Row.__slots__ if k != "segment"]
    return [_Row(r) for r in session.execute(select(*cols))]


def _apply(rows: list[_Row], f: dict, portal_ids: dict[str, int]) -> list[_Row]:
    out = rows
    if f.get("portal"):
        pid = portal_ids.get(f["portal"])
        out = [t for t in out if t.portal_id == pid]
    if f.get("type"):
        out = [t for t in out if t.opportunity_type == f["type"]]
    if f.get("segment"):
        out = [t for t in out if t.segment == f["segment"]]
    if f.get("capability"):
        out = [t for t in out if t.primary_capability == f["capability"]]
    return out


def build(session: Session, *, days: int = 90, portal: str | None = None, type: str | None = None,
          segment: str | None = None, capability: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    portals = {p.id: p for p in session.scalars(select(Portal))}
    portal_ids = {p.code: p.id for p in portals.values()}
    f = {"portal": portal, "type": type, "segment": segment, "capability": capability}
    all_rows = _load(session)
    first_seen = min((t.first_seen_at for t in all_rows if t.first_seen_at), default=None)
    history_days = (now - first_seen).total_seconds() / 86400 if first_seen else 0.0

    scoped = _apply(all_rows, f, portal_ids)
    since = now - timedelta(days=days) if days else None
    cur = [t for t in scoped if since is None or (t.first_seen_at and t.first_seen_at >= since)]
    prev = [t for t in scoped if since and t.first_seen_at and since - timedelta(days=days) <= t.first_seen_at < since]
    prev_valid = bool(since) and first_seen is not None and first_seen <= since - timedelta(days=days) + timedelta(days=1)

    ids = [t.id for t in cur]
    outcomes = {o.tender_id: o for o in session.scalars(select(BidOutcome))} if ids else {}
    cur_surfaced = [t for t in cur if t.decision in SURFACED]
    open_surfaced = [t for t in cur_surfaced if t.closing_at is None or t.closing_at >= now]

    def kpis_for(rows: list[_Row]) -> dict:
        surf = [t for t in rows if t.decision in SURFACED]
        open_ = [t for t in surf if t.closing_at is None or t.closing_at >= now]
        stages = Counter(outcomes[t.id].stage for t in rows if t.id in outcomes)
        decided = stages["WON"] + stages["LOST"]
        scores = [t.score for t in surf if t.score is not None]
        return {
            "read": len(rows), "surfaced": len(surf), "surfaced_rate": _rate(len(surf), len(rows)),
            "open_value": sum(_value(t) or 0 for t in open_), "open_count": len(open_),
            "open_with_value": sum(1 for t in open_ if _value(t)),
            "avg_score": round(statistics.mean(scores), 1) if scores else None,
            "submitted": stages["SUBMITTED"] + decided, "won": stages["WON"], "decided": decided,
            "win_rate": _rate(stages["WON"], decided),
            "closing_7d": sum(1 for t in open_ if t.closing_at and t.closing_at <= now + timedelta(days=7)),
        }

    k_cur, k_prev = kpis_for(cur), (kpis_for(prev) if prev_valid else None)
    series = _series(cur, now, days, first_seen)

    def kpi(key, label, value, *, fmt="int", note=None, spark=None, invert=False, prev_key=None):
        p = k_prev.get(prev_key or key) if k_prev else None
        delta = None
        if p not in (None, 0) and value is not None:
            delta = round((value - p) / p, 3)
        return {"key": key, "label": label, "value": value, "prev": p, "delta": delta, "format": fmt, "note": note,
                "spark": spark, "lower_is_better": invert}

    kpis = [
        kpi("read", "Tenders read", k_cur["read"], spark=[b["read"] for b in series["buckets"]], note="all sources"),
        kpi("surfaced", "Surfaced to the team", k_cur["surfaced"], spark=[b["surfaced"] for b in series["buckets"]],
            note=f"{_pct(k_cur['surfaced_rate'])} of those read"),
        kpi("open_value", "Open pipeline value", k_cur["open_value"], fmt="inr",
            note=f"{k_cur['open_with_value']} of {k_cur['open_count']} open opportunities state a value"),
        kpi("closing_7d", "Closing within 7 days", k_cur["closing_7d"], note="open, surfaced opportunities"),
        kpi("avg_score", "Average score", k_cur["avg_score"], fmt="score", note="of surfaced tenders, out of 100"),
        kpi("win_rate", "Win rate", k_cur["win_rate"], fmt="pct",
            note=(f"{k_cur['won']} won of {k_cur['decided']} decided" + (", too few to read much into" if k_cur["decided"] < 10 else ""))
            if k_cur["decided"] else "no bid results recorded yet"),
    ]

    # funnel
    prefilter_rejected = sum(1 for t in cur if t.analysis_mode == "PREFILTER")
    stages = Counter(outcomes[t.id].stage for t in cur if t.id in outcomes)
    funnel = [
        {"stage": "Read", "count": len(cur)},
        {"stage": "Passed screening", "count": len(cur) - prefilter_rejected},
        {"stage": "Surfaced", "count": len(cur_surfaced)},
        {"stage": "Pursued", "count": sum(stages[s] for s in PURSUED_STAGES)},
        {"stage": "Submitted", "count": stages["SUBMITTED"] + stages["WON"] + stages["LOST"]},
        {"stage": "Won", "count": stages["WON"]},
    ]

    # mix
    type_mix = []
    for code in ("SERVICE", "OEM", "HYBRID"):
        rows = [t for t in cur_surfaced if t.opportunity_type == code]
        type_mix.append({"code": code, "label": TYPE_LABEL[code], "count": len(rows), "value": sum(_value(t) or 0 for t in rows)})

    def group(key, rows) -> list[dict]:
        agg: dict[str, dict] = {}
        for t in rows:
            k = key(t)
            if not k:
                continue
            a = agg.setdefault(k, {"label": k, "count": 0, "value": 0, "scores": []})
            a["count"] += 1
            a["value"] += _value(t) or 0
            if t.score is not None:
                a["scores"].append(t.score)
        out = sorted(agg.values(), key=lambda a: (-a["count"], -a["value"]))
        for a in out:
            s = a.pop("scores")
            a["avg_score"] = round(statistics.mean(s), 1) if s else None
        return out

    by_capability = group(lambda t: t.primary_capability, cur_surfaced)
    by_segment = group(lambda t: t.segment, cur_surfaced)

    value_bands = []
    for label, lo, hi in VALUE_BANDS:
        value_bands.append({"label": label, "count": sum(1 for t in cur_surfaced if (v := _value(t)) and v >= lo and (hi is None or v < hi))})
    value_bands.append({"label": "Not stated", "count": sum(1 for t in cur_surfaced if not _value(t))})

    deadlines = []
    for label, lo, hi in DEADLINE_BANDS:
        rows = [t for t in open_surfaced if t.closing_at and lo * 86400 <= (t.closing_at - now).total_seconds() < (hi or 10**6) * 86400]
        deadlines.append({"label": label, "count": len(rows), "value": sum(_value(t) or 0 for t in rows)})

    # sources
    src: dict[int, dict] = {}
    for t in cur:
        p = portals.get(t.portal_id)
        if p is None:
            continue
        a = src.setdefault(p.id, {"code": p.code, "name": p.name, "read": 0, "surfaced": 0, "scores": [],
                                  "status": p.last_status, "last_run_at": as_utc(p.last_run_at).isoformat() if p.last_run_at else None})
        a["read"] += 1
        if t.decision in SURFACED:
            a["surfaced"] += 1
            if t.score is not None:
                a["scores"].append(t.score)
    sources = sorted(src.values(), key=lambda a: (-a["surfaced"], -a["read"]))
    for a in sources:
        s = a.pop("scores")
        a["avg_score"] = round(statistics.mean(s), 1) if s else None
        a["yield"] = _rate(a["surfaced"], a["read"])

    # rejection reasons (latest version per tender)
    latest: dict[int, str] = {}
    idset = set(ids)
    for tid, code in session.execute(select(RejectionReason.tender_id, RejectionReason.code).order_by(RejectionReason.tender_version)):
        if tid in idset:
            latest[tid] = code
    rejections = [{"code": c, "label": REJECTION_LABELS.get(c, c.replace("_", " ").capitalize()), "count": n}
                  for c, n in Counter(latest.values()).most_common(8)]

    # top opportunities
    from app.api.routes import _brief  # one source of truth for the plain-language line
    top_rows = sorted(open_surfaced, key=lambda t: (-(t.score or 0), t.closing_at or now + timedelta(days=3650)))[:10]
    top_tenders = {t.id: t for t in session.scalars(select(Tender).where(Tender.id.in_([r.id for r in top_rows])))} if top_rows else {}
    top = [{"id": r.id, "title": r.title, "organization": r.organization, "score": r.score, "priority": r.priority,
            "type": r.opportunity_type, "capability": r.primary_capability, "value": _value(r),
            "closing_at": r.closing_at.isoformat() if r.closing_at else None, "decision": r.decision,
            "stage": outcomes[r.id].stage if r.id in outcomes else None,
            "plain_summary": _brief(top_tenders[r.id])["headline"] if r.id in top_tenders else None} for r in top_rows]

    pipeline = Counter(outcomes[t.id].stage for t in cur_surfaced if t.id in outcomes)
    pipeline_stages = [{"stage": s, "count": pipeline[s]} for s in ("CONSIDERING", "BIDDING", "SUBMITTED", "WON", "LOST", "NO_BID")]
    pipeline_stages.insert(0, {"stage": "NOT_STARTED", "count": sum(1 for t in open_surfaced if t.id not in outcomes)})

    reviews_open = session.scalars(select(ManualReview).where(ManualReview.status == "OPEN")).all()
    reviews_open = [r for r in reviews_open if r.tender_id in idset]

    result = {
        "as_of": now.isoformat(),
        "window": {"days": days, "since": since.isoformat() if since else None, "history_days": round(history_days, 1),
                   "bucket": series["bucket"], "has_previous": prev_valid},
        "filters": {"applied": {k: v for k, v in f.items() if v}, "options": _options(all_rows, portals)},
        "kpis": kpis,
        "trend": series["buckets"],
        "funnel": funnel,
        "type_mix": type_mix,
        "by_capability": by_capability[:10],
        "by_segment": by_segment[:10],
        "value_bands": value_bands,
        "deadlines": deadlines,
        "sources": sources,
        "rejections": rejections,
        "pipeline": pipeline_stages,
        "top": top,
    }
    result["insights"] = _insights(result, k_cur, k_prev, open_surfaced, reviews_open, outcomes, portals, now)
    return result


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _series(rows: list[_Row], now: datetime, days: int, first_seen: datetime | None) -> dict:
    history = (now - first_seen).days + 1 if first_seen else 1
    span = min(days or 10**6, max(14, history))  # don't draw 13 empty weeks before the first tender
    daily = span <= 31
    if daily:
        start = (now - timedelta(days=span - 1)).date()
        keys = [start + timedelta(days=i) for i in range(span)]
    else:
        span_start = (now - timedelta(days=span)).date()
        span_start -= timedelta(days=span_start.weekday())
        weeks = min(52, max(1, ((now.date() - span_start).days // 7) + 1))
        start = now.date() - timedelta(days=now.date().weekday()) - timedelta(weeks=weeks - 1)
        keys = [start + timedelta(weeks=i) for i in range(weeks)]
    buckets = {k.isoformat(): {"date": k.isoformat(), "read": 0, "surfaced": 0} for k in keys}
    for t in rows:
        if not t.first_seen_at:
            continue
        d = t.first_seen_at.date()
        key = (d if daily else d - timedelta(days=d.weekday())).isoformat()
        if key in buckets:
            buckets[key]["read"] += 1
            buckets[key]["surfaced"] += t.decision in SURFACED
    return {"bucket": "day" if daily else "week", "buckets": list(buckets.values())}


def _options(rows: list[_Row], portals: dict[int, Portal]) -> dict:
    used = {t.portal_id for t in rows}
    return {
        "portals": sorted(({"code": p.code, "name": p.name} for p in portals.values() if p.id in used), key=lambda p: p["name"]),
        "types": [{"code": c, "label": lbl} for c, lbl in TYPE_LABEL.items()],
        "segments": sorted({t.segment for t in rows if t.segment}),
        "capabilities": sorted({t.primary_capability for t in rows if t.primary_capability}),
    }


def _insights(r: dict, k: dict, kp: dict | None, open_surfaced, reviews_open, outcomes, portals, now) -> list[dict]:
    """Plain statements computed from the numbers on the page, most important first."""
    from app.documents.values import format_inr
    out: list[dict] = []
    soon = sorted((t for t in open_surfaced if t.closing_at and t.closing_at <= now + timedelta(days=7)),
                  key=lambda t: t.closing_at)
    untracked = [t for t in soon if t.id not in outcomes]
    if soon:
        val = sum(_value(t) or 0 for t in soon)
        out.append({"kind": "risk", "title": f"{len(soon)} open opportunit{'y closes' if len(soon) == 1 else 'ies close'} within 7 days",
                    "detail": (f"Worth {format_inr(val)} where stated. " if val else "") +
                              (f"{len(untracked)} {'has' if len(untracked) == 1 else 'have'} no bid decision recorded yet."
                               if untracked else "Every one has a bid status recorded."),
                    "tender_ids": [t.id for t in soon][:5]})
    hot = [t for t in open_surfaced if t.priority == "HOT" and t.id not in outcomes]
    if hot:
        out.append({"kind": "opportunity", "title": f"{len(hot)} HOT opportunit{'y' if len(hot) == 1 else 'ies'} not yet picked up",
                    "detail": "Highest-scoring open tenders with no bid record: start tracking them on the Bid pipeline.",
                    "tender_ids": [t.id for t in hot][:5]})
    if reviews_open:
        oldest = max((now - as_utc(x.created_at)).total_seconds() / 3600 for x in reviews_open)
        out.append({"kind": "risk" if oldest > 48 else "info",
                    "title": f"{len(reviews_open)} tender{'s' if len(reviews_open) != 1 else ''} waiting in the review queue",
                    "detail": f"The oldest has waited {_hours(oldest)}." + (" Decisions older than 2 days risk missing deadlines." if oldest > 48 else "")})
    srcs = [s for s in r["sources"] if s["read"] >= 50]
    if len(srcs) >= 2:
        best = max(srcs, key=lambda s: s["yield"] or 0)
        worst = min(srcs, key=lambda s: s["yield"] or 0)
        if best["yield"] and best is not worst:
            out.append({"kind": "info", "title": f"{best['name']} is the most productive source",
                        "detail": f"1 relevant tender for every {round(1 / best['yield'])} read, against "
                                  + (f"1 in {round(1 / worst['yield'])}" if worst["yield"] else "none")
                                  + f" for {worst['name']} ({worst['read']:,} read)."})
    for s in r["sources"]:
        if s["status"] in ("ERROR", "BLOCKED"):  # PARTIAL is normal: CAPTCHA-gated detail pages are expected
            out.append({"kind": "anomaly", "title": f"{s['name']}: last run {s['status'].lower().replace('_', ' ')}",
                        "detail": "Check Sources & rules; new tenders from this portal may be missing."})
    if kp:
        for key, label in (("read", "Tenders read"), ("surfaced", "Surfaced tenders")):
            a, b = k[key], kp[key]
            if b >= 10 and abs(a - b) / b >= 0.25:
                out.append({"kind": "trend", "title": f"{label} {'up' if a > b else 'down'} {abs(a - b) / b:.0%} on the previous period",
                            "detail": f"{a:,} against {b:,}."})
    vols = [b["read"] for b in r["trend"]]
    nonzero = [v for v in vols if v]
    if len(nonzero) >= 4:
        mean, sd = statistics.mean(nonzero), statistics.pstdev(nonzero)
        spikes = [b for b in r["trend"] if sd and (b["read"] - mean) / sd >= 2]
        for b in spikes[:1]:
            out.append({"kind": "anomaly", "title": f"Unusual volume in the {r['window']['bucket']} of {b['date']}",
                        "detail": f"{b['read']:,} tenders read against a typical {round(mean):,}. Often a backfill or a portal re-listing."})
    caps = r["by_capability"]
    if caps and caps[0]["count"] >= 3 and (len(caps) == 1 or caps[0]["count"] > caps[1]["count"]):
        top = caps[0]
        share = top["count"] / max(1, k["surfaced"])
        out.append({"kind": "info", "title": f"{top['label']} leads demand",
                    "detail": f"{top['count']} of {k['surfaced']} surfaced tenders ({share:.0%})"
                              + (f", average score {top['avg_score']:.0f}." if top["avg_score"] is not None else ".")})
    if k["decided"] < 10:
        out.append({"kind": "info", "title": "Not enough bid results for win-rate trends",
                    "detail": f"{k['decided']} bid{'s' if k['decided'] != 1 else ''} with a recorded result; win rates become meaningful from about 10."})
    if r["window"]["days"] and r["window"]["history_days"] < r["window"]["days"]:
        n = max(1, round(r["window"]["history_days"]))
        out.append({"kind": "info", "title": f"Only {n} day{'s' if n != 1 else ''} of history",
                    "detail": "The agent has been collecting for less than the selected period, so growth comparisons are not available yet."})
    return out[:8]


def _hours(h: float) -> str:
    return f"{round(h)} hours" if h < 48 else f"{round(h / 24)} days"

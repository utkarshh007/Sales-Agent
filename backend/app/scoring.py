"""Section 14 — explainable 0–100 relevance score with configurable weights.

Every component carries the points awarded, the maximum, and a plain-language reason. When a
component does not apply (OEM match for a service-only tender) it is excluded and the total is
normalised over the applicable maximum, so service tenders are not penalised for having no OEM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.analysis import Analysis, CapabilityMatch
from app.config import Settings
from app.db import as_utc, utcnow
from app.rules.commercial import CommercialDecision


@dataclass
class ScoreResult:
    total: float
    priority: str
    breakdown: dict[str, dict]
    weights: dict[str, float]


def priority_for(score: float, s: Settings) -> str:
    if score >= s.HOT_SCORE_THRESHOLD:
        return "HOT"
    if score >= s.HIGH_SCORE_THRESHOLD:
        return "HIGH"
    if score >= s.MEDIUM_SCORE_THRESHOLD:
        return "MEDIUM"
    if score >= s.LOW_SCORE_THRESHOLD:
        return "LOW"
    return "SUPPRESS"


def compute_score(
    *,
    settings: Settings,
    matches: list[CapabilityMatch],
    opportunity_type: str,
    commercial: CommercialDecision,
    analysis: Analysis | None,
    has_documents: bool,
    closing_at: datetime | None,
    organization: str | None,
    other_oems_named: list[str] | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    now = now or utcnow()
    w = {
        "capability": settings.WEIGHT_CAPABILITY, "technical": settings.WEIGHT_TECHNICAL,
        "oem": settings.WEIGHT_OEM, "commercial": settings.WEIGHT_COMMERCIAL,
        "eligibility": settings.WEIGHT_ELIGIBILITY, "timeline": settings.WEIGHT_TIMELINE,
        "strategic": settings.WEIGHT_STRATEGIC,
    }
    b: dict[str, dict] = {}

    def put(key: str, factor: float | None, reason: str) -> None:
        if factor is None:
            b[key] = {"points": None, "max": w[key], "applicable": False, "reason": reason}
        else:
            f = max(0.0, min(1.0, factor))
            b[key] = {"points": round(f * w[key], 1), "max": w[key], "applicable": True, "reason": reason}

    best = matches[0] if matches else None
    cap_factor = (best.confidence / 100) if best else 0.0
    if best and best.match_type == "ADJACENT":
        cap_factor *= 0.6
    put("capability", cap_factor,
        f"{best.match_type.title()} match to {best.capability_name} ({best.sub_capability}), confidence {best.confidence}."
        if best else "No capability match.")

    # Technical requirement coverage
    # Only LLM-itemised requirements count: rules-mode requirements are the lexicon hits themselves.
    llm = analysis is not None and analysis.mode == "LLM"
    reqs = [r for r in (analysis.requirements if llm else []) if r.kind in ("SERVICE", "PRODUCT")]
    if reqs:
        mapped = [r for r in reqs if r.match_type in ("DIRECT", "SEMANTIC") and r.capability_ids]
        partial = [r for r in reqs if r.match_type == "ADJACENT" and r.capability_ids]
        cov = (len(mapped) + 0.5 * len(partial)) / len(reqs)
        put("technical", cov, f"{len(mapped)} of {len(reqs)} extracted technical requirements map directly/semantically "
                              f"to company capabilities ({len(partial)} adjacent).")
    else:
        cap_pct = 0.75 if has_documents else 0.6
        basis = "documents (keyword evidence, requirements not itemised)" if has_documents else "title and listing metadata only (no documents yet)"
        put("technical", cap_pct * cap_factor,
            f"Technical coverage estimated from {basis}; capped at {int(cap_pct * 100)}% until requirements are itemised by the LLM.")

    # OEM / product match
    if opportunity_type in ("SERVICE", "UNRELATED"):
        put("oem", None, "Not applicable to a service-only opportunity; score normalised over the remaining weights."
            if opportunity_type == "SERVICE" else "Not applicable: no catalog requirement identified.")
    else:
        prod = [m for m in matches if m.offering == "PRODUCT"]
        explicit = [m for m in prod if m.explicit_oem]
        foreign = [o for o in (other_oems_named or []) if o]
        if explicit:
            put("oem", 1.0, f"Tender names supported product(s): {', '.join(sorted({p for m in explicit for p in m.explicit_product_ids}))}.")
        elif prod and prod[0].match_type in ("DIRECT", "SEMANTIC") and prod[0].product_ids:
            f = 0.75 if not foreign else 0.4
            extra = f" Tender also names non-portfolio OEM(s): {', '.join(foreign)}." if foreign else ""
            put("oem", f, f"Functional requirement for {prod[0].capability_name} matches the supported portfolio "
                          f"({', '.join(prod[0].product_ids)}).{extra}")
        elif prod:
            put("oem", 0.35, f"Only an adjacent product-category match ({prod[0].capability_name}).")
        else:
            put("oem", 0.1, "Product component present but no supported OEM/product category matches.")

    # Commercial suitability
    cs = commercial.status
    if cs == "ACCEPT":
        put("commercial", 1.0 if (commercial.value_known or opportunity_type == "OEM") else 0.6, commercial.reason)
    elif cs == "REVIEW":
        put("commercial", 0.5, commercial.reason)
    else:
        put("commercial", 0.0, commercial.reason)

    # Eligibility feasibility
    elig = analysis.eligibility_assessment if analysis else "UNKNOWN"
    issues = analysis.eligibility_issues if analysis else []
    ef = {"FEASIBLE": 1.0, "PARTIAL": 0.5, "INFEASIBLE": 0.0}.get(elig, 0.5)
    put("eligibility", ef, f"Eligibility assessed as {elig}." + (f" Issues: {'; '.join(issues[:3])}." if issues else ""))

    # Timeline feasibility
    closing = as_utc(closing_at)
    if closing is None:
        put("timeline", 0.5, "Closing date unknown.")
    else:
        days = (closing - now).total_seconds() / 86400
        tf = 1.0 if days >= 14 else 0.7 if days >= 7 else 0.4 if days >= 3 else 0.1
        put("timeline", tf, f"{days:.1f} days until bid submission closes.")

    # Strategic relevance
    sr = analysis.strategic_relevance if analysis else "UNKNOWN"
    if sr in ("HIGH", "MEDIUM", "LOW"):
        put("strategic", {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.2}[sr], f"Strategic relevance assessed as {sr}.")
    else:
        org = (organization or "").lower()
        hit = next((k for k in settings.strategic_keywords if k in org), None)
        put("strategic", 1.0 if hit else 0.5,
            f"Organisation matches strategic segment “{hit}”." if hit else "No strategic-segment signal.")

    applicable = [v for v in b.values() if v["applicable"]]
    max_pts = sum(v["max"] for v in applicable) or 1
    total = round(100 * sum(v["points"] for v in applicable) / max_pts, 1)
    return ScoreResult(total, priority_for(total, settings), b, w)

"""Section 14 — explainable 0–100 relevance score with configurable weights (v2, Phase 5).

Every component carries the points awarded, the maximum, and a plain-language reason. When a
component does not apply (OEM match for a service-only tender) it is excluded and the total is
normalised over the applicable maximum, so service tenders are not penalised for having no OEM.

v2 adds: eligibility checked against the company profile, EMD-based value estimates, timelines judged
against the preparation each opportunity type needs, buyer-segment strategy, and a list of next
actions with how many points each could add.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.analysis import Analysis, CapabilityMatch
from app.config import Settings
from app.db import as_utc, utcnow
from app.rules.commercial import CommercialDecision

# commercial factor by decision code; anything not listed falls back on status
COMMERCIAL_FACTOR = {
    "SERVICE_WITHIN_CAP": 1.0, "OEM_NO_CAP": 1.0, "HYBRID_COMPONENTS_OK": 1.0, "HYBRID_TOTAL_WITHIN_CAP": 1.0,
    "SERVICE_LIKELY_WITHIN_CAP": 0.8, "HYBRID_LIKELY_WITHIN_CAP": 0.8, "HYBRID_SPLIT_UNKNOWN": 0.6,
    "SERVICE_VALUE_UNKNOWN": 0.5, "HYBRID_REVIEW_REQUIRED": 0.5, "SERVICE_LIKELY_OVER_CAP": 0.2,
    "HYBRID_SERVICE_COMPONENT_OVER_CAP": 0.3,
}


@dataclass
class ScoreResult:
    total: float
    priority: str
    breakdown: dict[str, dict]
    weights: dict[str, float]
    next_actions: list[dict] = field(default_factory=list)


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
    has_portal_detail: bool = False,
    closing_at: datetime | None,
    organization: str | None,
    other_oems_named: list[str] | None = None,
    now: datetime | None = None,
    eligibility=None,  # app.rules.eligibility.EligibilityResult
    segment: tuple[str, float] | None = None,
    pre_bid_at: datetime | None = None,
    documents_blocked: bool = False,
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

    # Technical requirement coverage. Only LLM-itemised requirements count; rules-mode requirements are
    # the lexicon hits themselves, so coverage is capped by how much of the tender has been read.
    llm = analysis is not None and analysis.mode == "LLM"
    reqs = [r for r in (analysis.requirements if llm else []) if r.kind in ("SERVICE", "PRODUCT")]
    tech_cap = 1.0
    if reqs:
        mapped = [r for r in reqs if r.match_type in ("DIRECT", "SEMANTIC") and r.capability_ids]
        partial = [r for r in reqs if r.match_type == "ADJACENT" and r.capability_ids]
        cov = (len(mapped) + 0.5 * len(partial)) / len(reqs)
        put("technical", cov, f"{len(mapped)} of {len(reqs)} extracted technical requirements map directly/semantically "
                              f"to company capabilities ({len(partial)} adjacent).")
    else:
        if has_documents:
            tech_cap, basis = 0.75, "documents (keyword evidence, requirements not itemised)"
        elif has_portal_detail:
            tech_cap, basis = 0.68, "the portal's tender detail page (no documents yet)"
        else:
            tech_cap, basis = 0.6, "title and listing metadata only (no documents yet)"
        put("technical", tech_cap * cap_factor,
            f"Technical coverage estimated from {basis}; capped at {int(tech_cap * 100)}% until requirements are itemised by the LLM.")

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
    cf = COMMERCIAL_FACTOR.get(commercial.code)
    if cf is None:
        cf = {"ACCEPT": 1.0 if commercial.value_known else 0.6, "REVIEW": 0.5}.get(commercial.status, 0.0)
    put("commercial", cf, commercial.reason)

    # Eligibility feasibility — checked against the company profile when criteria were found
    if eligibility is not None and eligibility.criteria:
        gaps, unknown = eligibility.gaps, eligibility.unknowns
        checked = [c for c in eligibility.criteria if c.status in ("MET", "RELAXED")]
        if gaps:
            ef = 0.0
            reason = "Not eligible: " + "; ".join(f"{c.label} requires {c.requirement} (company: {c.company})" for c in gaps[:3]) + "."
        else:
            total = len(checked) + len(unknown)
            ef = 0.5 + 0.5 * (len(checked) / total) if total else 0.5
            reason = f"{len(checked)} of {total} eligibility criteria met" + \
                     (f"; not checked: {', '.join(c.label for c in unknown[:4])} (company profile incomplete)." if unknown else ".")
        put("eligibility", ef, reason)
    else:
        elig = analysis.eligibility_assessment if analysis else "UNKNOWN"
        issues = analysis.eligibility_issues if analysis else []
        ef = {"FEASIBLE": 1.0, "PARTIAL": 0.5, "INFEASIBLE": 0.0}.get(elig, 0.5)
        put("eligibility", ef, (f"Eligibility assessed as {elig}." if elig != "UNKNOWN"
                                else "Eligibility criteria not available yet (no tender documents read).")
            + (f" Issues: {'; '.join(issues[:3])}." if issues else ""))

    # Timeline feasibility — against the preparation each opportunity type needs
    lead = {"SERVICE": settings.LEAD_DAYS_SERVICE, "OEM": settings.LEAD_DAYS_OEM,
            "HYBRID": settings.LEAD_DAYS_HYBRID}.get(opportunity_type, settings.LEAD_DAYS_OEM)
    closing = as_utc(closing_at)
    if closing is None:
        put("timeline", 0.5, "Closing date unknown.")
    else:
        days = (closing - now).total_seconds() / 86400
        tf = 1.0 if days >= lead else 0.6 if days >= lead / 2 else 0.3 if days >= 2 else 0.05
        put("timeline", tf, f"{days:.1f} days until bid submission closes; a {opportunity_type.lower()} bid "
                            f"typically needs about {lead} days.")

    # Strategic relevance — buyer segment, raised (never lowered) by the LLM's judgement
    seg_name, seg_w = segment or ("Other", 0.4)
    sr = analysis.strategic_relevance if analysis else "UNKNOWN"
    llm_w = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.2}.get(sr)
    sf = max(seg_w, llm_w or 0.0)
    put("strategic", sf, f"Buyer segment: {seg_name}." + (f" LLM assessed strategic relevance as {sr}." if llm_w else ""))

    applicable = [v for v in b.values() if v["applicable"]]
    max_pts = sum(v["max"] for v in applicable) or 1
    total = round(100 * sum(v["points"] for v in applicable) / max_pts, 1)
    actions = next_actions(b, max_pts, commercial=commercial, eligibility=eligibility, has_documents=has_documents,
                           documents_blocked=documents_blocked, cap_factor=cap_factor,
                           other_oems_named=other_oems_named or [], matches=matches, pre_bid_at=pre_bid_at, now=now)
    return ScoreResult(total, priority_for(total, settings), b, w, actions)


def next_actions(b: dict[str, dict], max_pts: float, *, commercial: CommercialDecision, eligibility,
                 has_documents: bool, documents_blocked: bool, cap_factor: float, other_oems_named: list[str],
                 matches: list[CapabilityMatch], pre_bid_at: datetime | None, now: datetime) -> list[dict]:
    """What a person can do next, with how many score points it could add (0–100 scale)."""
    def gain(component: str, new_factor: float) -> float:
        c = b.get(component)
        if not c or not c["applicable"]:
            return 0.0
        return round(max(0.0, new_factor * c["max"] - c["points"]) * 100 / max_pts, 1)

    out: list[dict] = []
    if not has_documents:
        # documents lift the technical-coverage cap to 75% (more once the LLM itemises requirements)
        out.append({"action": "Get the tender documents and upload them" + (" (the portal needs a CAPTCHA)" if documents_blocked else ""),
                    "why": "Requirements, eligibility and value are read from the documents.",
                    "points": gain("technical", 0.75 * cap_factor)})
    if commercial.code in ("SERVICE_VALUE_UNKNOWN", "SERVICE_LIKELY_WITHIN_CAP", "SERVICE_LIKELY_OVER_CAP"):
        out.append({"action": "Confirm the estimated contract value", "why": "Settles the ₹30 lakh service rule.",
                    "points": gain("commercial", 1.0)})
    if commercial.code == "HYBRID_REVIEW_REQUIRED":
        out.append({"action": "Find the service vs product split (price-bid format or BOQ)",
                    "why": "Settles the service-value rule for this hybrid tender.", "points": gain("commercial", 1.0)})
    if eligibility is not None:
        missing = [c for c in eligibility.unknowns if c.code != "OEM_AUTHORISATION"]
        if missing:
            out.append({"action": "Complete the company profile: " + ", ".join(sorted({c.label for c in missing})),
                        "why": "These eligibility criteria could not be checked.", "points": gain("eligibility", 1.0)})
        for c in eligibility.gaps[:3]:
            out.append({"action": f"Eligibility gap: {c.label} ({c.requirement}; company: {c.company})",
                        "why": "A partner or consortium may help if the tender allows it.", "points": gain("eligibility", 1.0)})
        if any(c.code == "OEM_AUTHORISATION" and c.status != "MET" for c in eligibility.criteria):
            prods = matches[0].product_ids[:3] if matches else []
            out.append({"action": "Line up an OEM authorisation (MAF)" + (f" for {', '.join(prods)}" if prods else ""),
                        "why": "The tender requires a manufacturer's authorisation.", "points": 0.0})
    if other_oems_named:
        out.append({"action": f"Check whether {', '.join(other_oems_named[:3])} is mandatory or 'or equivalent' is allowed",
                    "why": "The tender names a product the company does not sell.", "points": gain("oem", 0.75)})
    pre_bid = as_utc(pre_bid_at)
    if pre_bid is not None and pre_bid > now:
        out.append({"action": f"Attend the pre-bid meeting on {pre_bid:%d %b %Y} and send queries",
                    "why": "Clarifications can change scope, eligibility and value.", "points": 0.0})
    return out

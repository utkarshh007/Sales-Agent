"""The portal-agnostic intelligence engine: one pure function from TenderContext to Decision.

    prefilter (deterministic, no tokens) -> analyzer (LLM or rules) -> matching -> classification
    -> commercial value rules -> relevance gate -> scoring -> decision

No database access here, so every business rule is unit-testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.analysis import Analysis, CapabilityMatch, TenderContext
from app.catalog import Catalog
from app.config import AdjacentAction, Settings
from app.documents.values import format_inr
from app.llm.analyzer import Analyzer, RulesAnalyzer
from app.matching import build_matches, relevance_gate
from app.rules.classification import classify_components, component_values
from app.rules.commercial import CommercialDecision, apply_commercial_rules
from app.rules.eligibility import EligibilityResult, assess, extract_criteria
from app.rules.hybrid import ValueBand, boq_components, emd_value_band
from app.rules.prefilter import PrefilterResult, prefilter
from app.scoring import ScoreResult, compute_score

ACCEPTED, REJECTED, MANUAL_REVIEW = "ACCEPTED", "REJECTED", "MANUAL_REVIEW"


@dataclass
class Decision:
    status: str
    opportunity_type: str
    prefilter: PrefilterResult
    analysis: Analysis | None = None
    matches: list[CapabilityMatch] = field(default_factory=list)
    commercial: CommercialDecision | None = None
    score: ScoreResult | None = None
    rejections: list[tuple[str, str, str]] = field(default_factory=list)  # (stage, code, detail)
    reviews: list[tuple[str, str]] = field(default_factory=list)  # (code, reason)
    relevance_reason: str = ""
    flags: list[str] = field(default_factory=list)
    value_analysis: dict = field(default_factory=dict)
    eligibility: EligibilityResult | None = None
    segment: tuple[str, float] | None = None

    @property
    def priority(self) -> str | None:
        if self.status == REJECTED or self.score is None:
            return "SUPPRESS" if self.status == REJECTED else None
        # a tender kept for review is never labelled "suppressed", however low its score
        return "LOW" if self.score.priority == "SUPPRESS" else self.score.priority

    @property
    def primary_reason(self) -> str:
        if self.rejections:
            return self.rejections[0][2]
        if self.reviews:
            return self.reviews[0][1]
        return self.relevance_reason


def _resolve_values(pf: PrefilterResult, analysis: Analysis, opp_type: str) -> tuple[int | None, int | None, int | None, dict, list[str]]:
    """Deterministic value wins when stated plainly; LLM value is used otherwise. Disagreement is flagged."""
    flags: list[str] = []
    det = pf.value.amount_inr if pf.value else None
    llm = analysis.total_value_inr if analysis.mode == "LLM" else None
    total = det if det is not None else llm
    if det is not None and llm is not None and abs(det - llm) > 0.1 * max(det, llm):
        flags.append("VALUE_DISAGREEMENT")
    sv, pv, _complete = component_values(analysis.components)
    if opp_type == "SERVICE" and sv is None:
        sv = total
    if opp_type == "OEM" and pv is None:
        pv = total
    # For HYBRID we never derive service = total - product: the service value must be stated.
    va = {
        "deterministic_value_inr": det,
        "deterministic_evidence": pf.value.evidence if pf.value else None,
        "llm_value_inr": llm,
        "llm_evidence": (analysis.total_value_evidence or None) if analysis.mode == "LLM" else None,
        "total_value_inr": total,
        "service_value_inr": sv,
        "product_value_inr": pv,
        "components": [{"description": c.description, "kind": c.kind, "value_inr": c.value_inr, "evidence": c.evidence}
                       for c in analysis.components],
        "emd_inr": pf.emd.amount_inr if pf.emd else None,
    }
    return total, sv, pv, va, flags


def evaluate(ctx: TenderContext, catalog: Catalog, settings: Settings, analyzer: Analyzer,
             now: datetime | None = None) -> Decision:
    pf = prefilter(ctx, catalog, settings, now)
    if pf.decision == "REJECT":
        opp = {"SERVICE_OVER_CAP": "SERVICE", "UNRELATED": "UNRELATED"}.get(pf.code, pf.heuristic_type)
        return Decision(REJECTED, opp, pf, rejections=[("PREFILTER", pf.code, pf.reason)],
                        value_analysis={"deterministic_value_inr": pf.value.amount_inr if pf.value else None,
                                        "deterministic_evidence": pf.value.evidence if pf.value else None})

    hints = {"lexicon_hits": pf.lexicon_hits, "oem_hits": pf.oem_hits, "value": pf.value, "emd": pf.emd,
             "heuristic_type": pf.heuristic_type, "semantic_hits": pf.semantic_hits,
             "competitor_hits": pf.competitor_hits}
    analysis = analyzer.analyze(ctx, hints)
    reviews: list[tuple[str, str]] = []
    if analysis.refused:
        reviews.append(("LLM_DECLINED", "; ".join(analysis.notes) or "LLM declined to analyse the tender."))
        refused_notes = analysis.notes
        analysis = RulesAnalyzer(settings, catalog).analyze(ctx, hints)
        analysis.notes = refused_notes + analysis.notes

    matches = build_matches(catalog, pf.lexicon_hits, pf.oem_hits, analysis, pf.semantic_hits, pf.competitor_hits)
    # A priced bill of quantities gives a deterministic service/product split; it is preferred over
    # heuristics, and fills in values the LLM could not find.
    boq = boq_components(ctx.document_text, catalog) if ctx.has_documents else []
    if boq and (analysis.mode != "LLM" or not any(c.value_inr for c in analysis.components)):
        analysis.components = boq
    if (analysis.mode == "LLM" or boq) and analysis.components:
        opp_type = classify_components(analysis.components)
    else:
        opp_type = pf.heuristic_type
    if analysis.mode == "LLM" and analysis.requirements and all(r.match_type == "NONE" for r in analysis.requirements) \
            and not any(m.explicit_oem for m in matches):
        opp_type = "UNRELATED"

    total, sv, pv, va, flags = _resolve_values(pf, analysis, opp_type)
    band: ValueBand | None = emd_value_band(pf.emd.amount_inr) if pf.emd and total is None else None
    if band is not None:
        va["estimated_band"] = {"low": band.low, "high": band.high, "basis": band.basis}
    if boq:
        va["boq_lines"] = len(boq)
        flags.append("BOQ_SPLIT")
    rejections: list[tuple[str, str, str]] = []

    gate_status, gate_code, gate_reason = relevance_gate(matches, settings.STRONG_MATCH_MIN_CONFIDENCE,
                                                         analysis.generic_security_only)
    if opp_type == "UNRELATED" or not matches:
        opp_type = "UNRELATED"
        if gate_status != "REJECT":
            gate_status, gate_code, gate_reason = "REJECT", "NO_CAPABILITY_MATCH", "Requirements do not map to the capability catalog."

    commercial = apply_commercial_rules(opp_type, settings, total_value_inr=total, service_value_inr=sv,
                                        product_value_inr=pv, value_band=band) if opp_type != "UNRELATED" else None

    # Eligibility: criteria from the tender, checked against the company profile
    eligibility = None
    if ctx.eligibility_text:
        criteria, startup, msme = extract_criteria(ctx.eligibility_text)
        products = next((m.product_ids for m in matches if m.offering == "PRODUCT" and m.product_ids), [])
        eligibility = assess(criteria, ctx.company_profile or {}, startup_relaxation=startup, msme_relaxation=msme,
                             matched_products=products, source=ctx.eligibility_source)
        det = eligibility.assessment
        if det == "INFEASIBLE" or analysis.eligibility_assessment in ("UNKNOWN", "", None) or analysis.mode != "LLM":
            if det != "UNKNOWN":
                analysis.eligibility_assessment = det
        analysis.eligibility_issues = [f"{c.label}: requires {c.requirement}; company has {c.company}" for c in eligibility.gaps] \
            + analysis.eligibility_issues
        if eligibility.gaps:
            flags.append("ELIGIBILITY_GAP")
    segment = catalog.buyer_segment(ctx.organization, ctx.metadata.get("department"))

    other_oems = sorted({o for r in analysis.requirements for o in r.other_oems_named} | {c.oem for c in pf.competitor_hits})
    if other_oems:
        flags.append("NON_PORTFOLIO_OEM_SPECIFIED")
    score = compute_score(settings=settings, matches=matches, opportunity_type=opp_type,
                          commercial=commercial or apply_commercial_rules("UNKNOWN", settings, total_value_inr=total),
                          analysis=analysis, has_documents=ctx.has_documents,
                          has_portal_detail=ctx.has_portal_detail, closing_at=ctx.closing_at,
                          organization=ctx.organization, other_oems_named=other_oems, now=now,
                          eligibility=eligibility, segment=segment, pre_bid_at=ctx.pre_bid_at,
                          documents_blocked=ctx.documents_blocked)

    if gate_status == "REJECT":
        rejections.append(("RELEVANCE", gate_code, gate_reason))
    elif gate_status == "REVIEW":
        if settings.ADJACENT_MATCH_ACTION == AdjacentAction.SUPPRESS:
            rejections.append(("RELEVANCE", gate_code, gate_reason))
        else:
            reviews.append((gate_code, gate_reason))
    if commercial is not None:
        flags += commercial.flags
        if commercial.status == "REJECT":
            rejections.append(("COMMERCIAL", commercial.code, commercial.reason))
        elif commercial.status == "REVIEW":
            reviews.append((commercial.code, commercial.reason))
    # Adjacent-only matches score low by construction; when configured for review, a human decides
    # rather than the score threshold silently suppressing them.
    adjacent_review = gate_status == "REVIEW" and settings.ADJACENT_MATCH_ACTION != AdjacentAction.SUPPRESS
    if not rejections and not adjacent_review and score.total < settings.MIN_RELEVANCE_SCORE:
        rejections.append(("SCORE", "BELOW_MIN_SCORE",
                           f"Relevance score {score.total} is below the minimum {settings.MIN_RELEVANCE_SCORE}."))
    if not ctx.has_documents:
        flags.append("NO_DOCUMENTS")

    status = REJECTED if rejections else MANUAL_REVIEW if reviews else ACCEPTED
    relevance = gate_reason
    if matches and gate_status != "REJECT":
        top = matches[0]
        prods = ", ".join(catalog.products[p].name for p in top.product_ids[:4] if p in catalog.products)
        relevance = f"{gate_reason} {top.explanation}" + (f" Portfolio: {prods}." if prods else "")
        if commercial is not None:
            relevance += f" Commercial: {commercial.reason}"
    va["total_value_display"] = format_inr(total)
    return Decision(status, opp_type, pf, analysis, matches, commercial, score, rejections, reviews,
                    relevance.strip(), sorted(set(flags)), va, eligibility, segment)

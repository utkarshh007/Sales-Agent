"""LLM analysis worker (section 11/22) plus a deterministic rules-only analyzer.

ClaudeAnalyzer is used when ANTHROPIC_API_KEY is configured; RulesAnalyzer otherwise (or as the
documented fallback). Both return the same Analysis type, and the mode is recorded for audit.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

import anthropic

from app.analysis import FIELD_NAMES, UNKNOWN, Analysis, Component, FieldValue, Requirement, TenderContext
from app.catalog import Catalog, LexiconHit, OemHit
from app.config import Settings
from app.documents.values import ValueCandidate, format_inr
from app.llm.prompts import system_prompt, user_prompt
from app.llm.schema import analysis_schema

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """Transient API failure — the job is retried."""


class Analyzer(Protocol):
    def analyze(self, ctx: TenderContext, hints: dict) -> Analysis: ...


def _clamp(v: object) -> int:
    try:
        return max(0, min(100, int(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _to_int(v: object) -> int | None:
    if v is None:
        return None
    try:
        n = int(round(float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_analysis(data: dict, model: str) -> Analysis:
    a = Analysis(mode="LLM", model_version=model)
    for f in data.get("fields", []):
        if f.get("name") in FIELD_NAMES:
            val = (f.get("value") or "").strip() or UNKNOWN
            a.fields[f["name"]] = FieldValue(val, _clamp(f.get("confidence")) if val != UNKNOWN else 0, f.get("evidence") or "")
    for name in FIELD_NAMES:
        a.fields.setdefault(name, FieldValue())
    for r in data.get("requirements", []):
        a.requirements.append(Requirement(
            text=r.get("text", ""), kind=r.get("kind", "OTHER"), capability_ids=list(r.get("capability_ids", [])),
            sub_capability=r.get("sub_capability", ""), match_type=r.get("match_type", "NONE"),
            confidence=_clamp(r.get("confidence")), evidence=r.get("evidence", ""), explanation=r.get("explanation", ""),
            explicit_product_ids=list(r.get("explicit_product_ids", [])), other_oems_named=list(r.get("other_oems_named", [])),
        ))
    for c in data.get("components", []):
        a.components.append(Component(c.get("description", ""), c.get("kind", "SERVICE"), _to_int(c.get("value_inr")), c.get("evidence", "")))
    a.total_value_inr = _to_int(data.get("total_value_inr"))
    a.total_value_evidence = data.get("total_value_evidence", "")
    a.eligibility_assessment = data.get("eligibility_assessment", UNKNOWN)
    a.eligibility_issues = list(data.get("eligibility_issues", []))
    a.strategic_relevance = data.get("strategic_relevance", UNKNOWN)
    a.generic_security_only = bool(data.get("generic_security_only", False))
    a.summary = data.get("summary", "")
    a.recommendation = data.get("recommendation", "")
    a.risks = list(data.get("risks", []))
    return a


class ClaudeAnalyzer:
    def __init__(self, settings: Settings, catalog: Catalog, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.catalog = catalog
        self.client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, max_retries=3)
        self._system = system_prompt(catalog)
        self._schema = analysis_schema(catalog)

    def analyze(self, ctx: TenderContext, hints: dict) -> Analysis:
        model = self.settings.LLM_MODEL
        kwargs: dict = dict(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt(ctx)}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.LLM_EFFORT, "format": {"type": "json_schema", "schema": self._schema}},
        )
        if self.settings.LLM_FALLBACKS_ENABLED:
            # Tender text about red teaming / offensive tooling can trip safety classifiers; let the
            # API re-route a declined request instead of failing the analysis.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            kwargs["betas"] = []
        try:
            resp = self.client.beta.messages.create(**kwargs)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            raise LLMUnavailable(str(e)) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMUnavailable(str(e)) from e
            raise

        served_by = getattr(resp, "model", model) or model
        if resp.stop_reason == "refusal":
            a = Analysis(mode="LLM", model_version=served_by, refused=True)
            details = getattr(resp, "stop_details", None)
            a.notes.append(f"LLM declined to analyse this tender ({getattr(details, 'category', None) or 'no category'}).")
            return a
        if resp.stop_reason == "max_tokens":
            raise LLMUnavailable("LLM output hit max_tokens before completing the JSON")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMUnavailable(f"LLM returned invalid JSON: {e}") from e
        a = parse_analysis(data, served_by)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            a.notes.append(f"tokens in={usage.input_tokens} out={usage.output_tokens} "
                           f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}")
        return a


class RulesAnalyzer:
    """Deterministic analysis from portal metadata, lexicon hits and parsed values. Used when the LLM
    is disabled/unconfigured. Never fabricates: everything not found is UNKNOWN."""

    def __init__(self, settings: Settings, catalog: Catalog):
        self.settings = settings
        self.catalog = catalog

    def analyze(self, ctx: TenderContext, hints: dict) -> Analysis:
        hits: list[LexiconHit] = hints.get("lexicon_hits", [])
        oems: list[OemHit] = hints.get("oem_hits", [])
        value: ValueCandidate | None = hints.get("value")
        emd: ValueCandidate | None = hints.get("emd")
        opp_type: str = hints.get("heuristic_type", "UNKNOWN")
        a = Analysis(mode="RULES_ONLY", model_version="rules-v1")
        for name in FIELD_NAMES:
            a.fields[name] = FieldValue()

        def portal(name: str, val: object) -> None:
            if val:
                a.fields[name] = FieldValue(str(val), 100, "portal listing", "PORTAL")

        portal("tender_title", ctx.title)
        portal("tender_reference", ctx.reference_number)
        portal("procuring_organization", ctx.organization)
        portal("closing_date", ctx.closing_at.isoformat() if ctx.closing_at else None)
        portal("publication_date", ctx.published_at.isoformat() if ctx.published_at else None)
        portal("location", ctx.location)
        if value:
            a.fields["tender_value"] = FieldValue(format_inr(value.amount_inr), 85, value.evidence, "DETERMINISTIC")
            a.total_value_inr, a.total_value_evidence = value.amount_inr, value.evidence
        if emd:
            a.fields["emd"] = FieldValue(format_inr(emd.amount_inr), 85, emd.evidence, "DETERMINISTIC")
        if oems:
            a.fields["required_oems"] = FieldValue(", ".join(self.catalog.products[o.product_id].name for o in oems),
                                                   90, oems[0].evidence, "DETERMINISTIC")

        for h in hits:
            cap = self.catalog.capabilities[h.capability_id]
            a.requirements.append(Requirement(
                text=h.sub_capability, kind=cap.offering, capability_ids=[cap.id], sub_capability=h.sub_capability,
                match_type=h.match_type, confidence=h.confidence, evidence=h.evidence,
                explanation=f"Lexicon rule matched “{h.sub_capability}” ({h.match_type.lower()}).",
                explicit_product_ids=[o.product_id for o in oems if cap.id in self.catalog.products[o.product_id].capabilities],
            ))
        for sh in hints.get("semantic_hits", []):
            cap = self.catalog.capabilities[sh.capability_id]
            a.requirements.append(Requirement(
                text=ctx.title, kind=cap.offering, capability_ids=[cap.id], sub_capability=sh.anchor,
                match_type=sh.match_type, confidence=sh.confidence, evidence=ctx.title,
                explanation=f"Semantic similarity {sh.similarity:.2f} to “{sh.anchor}”."))
        competitors = hints.get("competitor_hits", [])
        for ch in competitors:
            cap = self.catalog.capabilities[ch.capability_id]
            a.requirements.append(Requirement(
                text=ch.evidence, kind=cap.offering, capability_ids=[cap.id], sub_capability=cap.name,
                match_type="SEMANTIC", confidence=80, evidence=ch.evidence,
                explanation=f"Names competitor product {ch.oem}.", other_oems_named=[ch.oem]))
        if competitors:
            a.risks.append("Tender names non-portfolio OEM(s): " + ", ".join(sorted({c.oem for c in competitors}))
                           + ("; an equivalent product is allowed." if all(c.or_equivalent for c in competitors)
                              else "; check whether the brand is mandatory."))
        # Components from the heuristic classification; values only when unambiguous.
        if opp_type == "SERVICE":
            a.components.append(Component("Services (from matched service capabilities)", "SERVICE", a.total_value_inr))
        elif opp_type == "OEM":
            a.components.append(Component("Product / OEM supply", "PRODUCT", a.total_value_inr))
        elif opp_type == "HYBRID":
            a.components += [Component("Product / OEM supply", "PRODUCT", None), Component("Associated services", "SERVICE", None)]
        org = (ctx.organization or "").lower()
        a.strategic_relevance = "UNKNOWN" if not any(k in org for k in self.settings.strategic_keywords) else "HIGH"
        a.generic_security_only = not hits and not oems and not hints.get("semantic_hits") and not competitors
        names = sorted({self.catalog.capabilities[h.capability_id].name for h in hits})
        a.summary = (f"Rules-only analysis of “{ctx.title}”. Matched: {', '.join(names) or 'no specific capability'}."
                     + ("" if ctx.has_documents else " No tender documents available yet."))
        a.recommendation = ("Review the tender documents to confirm scope, value and eligibility."
                            if names else "Insufficient evidence of a catalog match.")
        a.notes.append("LLM not configured — deterministic rules-only analysis.")
        return a


def build_analyzer(settings: Settings, catalog: Catalog) -> Analyzer:
    if settings.llm_available:
        return ClaudeAnalyzer(settings, catalog)
    return RulesAnalyzer(settings, catalog)

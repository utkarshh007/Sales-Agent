"""Section 10 — cheap deterministic filtering before any LLM tokens are spent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.analysis import TenderContext
from app.catalog import Catalog, LexiconHit, OemHit
from app.config import Settings
from app.db import as_utc, utcnow
from app.documents.values import ValueCandidate, best_value, format_inr
from app.rules.classification import heuristic_type


@dataclass
class PrefilterResult:
    decision: str  # REJECT | CONTINUE
    code: str = ""
    reason: str = ""
    lexicon_hits: list[LexiconHit] = field(default_factory=list)
    oem_hits: list[OemHit] = field(default_factory=list)
    exclusions: list[tuple[str, str]] = field(default_factory=list)
    generic_signal: bool = False
    value: ValueCandidate | None = None
    emd: ValueCandidate | None = None
    heuristic_type: str = "UNKNOWN"

    @property
    def strong_hits(self) -> list[LexiconHit]:
        return [h for h in self.lexicon_hits if h.match_type in ("DIRECT", "SEMANTIC")]


def deterministic_value(ctx: TenderContext) -> tuple[ValueCandidate | None, ValueCandidate | None]:
    value = (ValueCandidate("TENDER_VALUE", ctx.portal_value_inr, "portal listing field", True)
             if ctx.portal_value_inr else best_value(ctx.document_text, "TENDER_VALUE"))
    emd = (ValueCandidate("EMD", ctx.portal_emd_inr, "portal listing field", True)
           if ctx.portal_emd_inr else best_value(ctx.document_text, "EMD"))
    return value, emd


def _body_hits_are_material(hits: list[LexiconHit]) -> list[LexiconHit]:
    """A single passing mention deep inside a long document is not a requirement."""
    return [h for h in hits if h.in_title or h.occurrences >= 2 or h.match_type == "DIRECT"]


def prefilter(ctx: TenderContext, catalog: Catalog, settings: Settings, now: datetime | None = None) -> PrefilterResult:
    now = now or utcnow()
    closing = as_utc(ctx.closing_at)
    if closing is not None and closing < now:
        return PrefilterResult("REJECT", "CLOSED", f"Bid submission closed on {closing:%d-%b-%Y %H:%M} UTC")

    hits = _body_hits_are_material(catalog.lexicon_matches(ctx.title, ctx.document_text))
    oems = catalog.oem_mentions(ctx.title, ctx.document_text)
    excl = catalog.exclusion_hits(ctx.title)
    generic = catalog.has_generic_signal(ctx.title) or catalog.has_generic_signal(ctx.document_text[:20000])
    value, emd = deterministic_value(ctx)
    res = PrefilterResult("CONTINUE", lexicon_hits=hits, oem_hits=oems, exclusions=excl,
                          generic_signal=generic, value=value, emd=emd)
    res.heuristic_type = heuristic_type(ctx.title + "\n" + ctx.document_text[:30000], hits, oems, catalog)

    strong = res.strong_hits
    if not hits and not oems and not generic:
        res.decision, res.code = "REJECT", "UNRELATED"
        res.reason = "No capability, OEM or security signal in the tender title/documents (deterministic filter)."
        return res
    if excl and not strong and not oems:
        res.decision, res.code = "REJECT", "UNRELATED"
        res.reason = f"Non-cyber tender ({excl[0][0]}: “{excl[0][1]}”) with no direct/semantic capability match."
        return res

    # Service-only tender with a plainly stated value above the cap: reject without the LLM.
    if (settings.SERVICE_VALUE_RULE_ENABLED and res.heuristic_type == "SERVICE" and value is not None
            and value.amount_inr > settings.SERVICE_MAX_VALUE_INR and not oems):
        res.decision, res.code = "REJECT", "SERVICE_OVER_CAP"
        res.reason = (f"Service-only opportunity valued at {format_inr(value.amount_inr)} exceeds the "
                      f"{format_inr(settings.SERVICE_MAX_VALUE_INR)} service limit (deterministic; evidence: “{value.evidence}”).")
    return res

"""Section 10 — cheap deterministic filtering before any LLM tokens are spent."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.analysis import TenderContext
from app.catalog import Catalog, CompetitorHit, LexiconHit, OemHit
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
    semantic_hits: list = field(default_factory=list)  # app.semantic.SemanticHit
    competitor_hits: list[CompetitorHit] = field(default_factory=list)

    @property
    def strong_hits(self) -> list[LexiconHit]:
        return [h for h in self.lexicon_hits if h.match_type in ("DIRECT", "SEMANTIC")]


_WORK_DESCRIPTION = re.compile(r"^Work description: (.+)$", re.MULTILINE)


def semantic_text(title: str, document_text: str = "") -> str:
    """What the embedding model reads: the title, plus the portal's one-line work description when
    a detail page was read. Full documents are left to the lexicon and the LLM."""
    m = _WORK_DESCRIPTION.search(document_text or "")
    extra = m.group(1).strip() if m and m.group(1).strip().lower() != title.strip().lower() else ""
    return f"{title}. {extra}" if extra else title


def semantic_hits(text: str, catalog: Catalog, settings: Settings) -> list:
    from app.semantic import get_semantic_matcher
    matcher = get_semantic_matcher(catalog, settings)
    return matcher.match(text, settings=settings) if matcher is not None else []


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
    # the portal's product category ("Civil Works", "Electrical Works"…) is as telling as the title
    excl = catalog.exclusion_hits(ctx.title + (f" | {ctx.category}" if ctx.category else ""))
    generic = catalog.has_generic_signal(ctx.title) or catalog.has_generic_signal(ctx.document_text[:20000])
    value, emd = deterministic_value(ctx)
    competitors = catalog.competitor_mentions(ctx.title, ctx.document_text)
    # exclusions veto semantic similarity: CCTV or security guards are never a fuzzy cyber match
    sem = [] if excl else semantic_hits(semantic_text(ctx.title, ctx.document_text), catalog, settings)
    res = PrefilterResult("CONTINUE", lexicon_hits=hits, oem_hits=oems, exclusions=excl,
                          generic_signal=generic, value=value, emd=emd, semantic_hits=sem, competitor_hits=competitors)
    # Semantic similarity is fallback evidence for wording the lexicon can't read. When the lexicon or a
    # named product already gives strong evidence, embeddings must not reclassify the tender (they blur
    # e.g. a red-team *service* with adversary-simulation *tools*, turning a service into a hybrid).
    fallback = not hits and not oems
    extra_caps = ([h.capability_id for h in sem if h.match_type == "SEMANTIC"] if fallback else []) \
        + [c.capability_id for c in competitors]
    weak_caps = [h.capability_id for h in sem if h.match_type == "ADJACENT"] if fallback else []
    res.semantic_hits = sem if fallback else []  # only shown/used when it is the evidence
    res.heuristic_type = heuristic_type(ctx.title + "\n" + ctx.document_text[:30000], hits, oems, catalog,
                                        extra_caps, weak_caps)

    strong = res.strong_hits
    if not hits and not oems and not generic and not sem and not competitors:
        res.decision, res.code = "REJECT", "UNRELATED"
        res.reason = "No capability, OEM or security signal in the tender title/documents (deterministic filter)."
        return res
    if excl and not strong and not oems and not competitors:
        res.decision, res.code = "REJECT", "UNRELATED"
        res.reason = f"Non-cyber tender ({excl[0][0]}: “{excl[0][1]}”) with no direct/semantic capability match."
        return res

    # Service-only tender with a plainly stated value above the cap: reject without the LLM.
    if (settings.SERVICE_VALUE_RULE_ENABLED and res.heuristic_type == "SERVICE" and value is not None
            and value.amount_inr > settings.SERVICE_MAX_VALUE_INR and not oems and not competitors):
        res.decision, res.code = "REJECT", "SERVICE_OVER_CAP"
        res.reason = (f"Service-only opportunity valued at {format_inr(value.amount_inr)} exceeds the "
                      f"{format_inr(settings.SERVICE_MAX_VALUE_INR)} service limit (deterministic; evidence: “{value.evidence}”).")
    return res


def title_is_candidate(title: str, closing_at: datetime | None, catalog: Catalog, now: datetime | None = None,
                       settings: Settings | None = None) -> bool:
    """Cheap listing-level screen used to decide whether a portal detail page or documents are worth a
    request. Deliberately looser than prefilter(): any capability, OEM, competitor, semantic or generic
    security signal qualifies, unless the title is clearly non-cyber (exclusion) with no specific match."""
    closing = as_utc(closing_at)
    if closing is not None and closing < (now or utcnow()):
        return False
    hits = catalog.lexicon_matches(title)
    if catalog.oem_mentions(title) or catalog.competitor_mentions(title) \
            or any(h.match_type in ("DIRECT", "SEMANTIC") for h in hits):
        return True
    if catalog.exclusion_hits(title):
        return False
    if hits or catalog.has_generic_signal(title):
        return True
    from app.config import get_settings
    return bool(semantic_hits(title, catalog, settings or get_settings()))

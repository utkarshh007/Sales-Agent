"""Section 12/13 — company-capability and OEM matching.

Combines three evidence sources into one explainable list of CapabilityMatch:
  1. explicit OEM/product mentions (always DIRECT, both directions of section 13 case 1),
  2. the LLM's requirement-to-capability mapping (semantic understanding; IDs constrained to the catalog),
  3. the deterministic lexicon (direct/semantic/adjacent synonyms).
Functional requirements are expanded to the supported portfolio for that capability (case 2/3).
"""
from __future__ import annotations

from app.analysis import Analysis, CapabilityMatch
from app.catalog import MATCH_TYPE_RANK, Catalog, LexiconHit, OemHit


def _portfolio(catalog: Catalog, capability_id: str) -> list[str]:
    return [p.id for p in catalog.products_for(capability_id)]


def build_matches(
    catalog: Catalog,
    lexicon_hits: list[LexiconHit],
    oem_hits: list[OemHit],
    analysis: Analysis | None,
) -> list[CapabilityMatch]:
    merged: dict[str, CapabilityMatch] = {}
    llm_ran = analysis is not None and analysis.mode == "LLM" and not analysis.refused

    def put(m: CapabilityMatch) -> None:
        cur = merged.get(m.capability_id)
        if cur is None:
            merged[m.capability_id] = m
            return
        better = (MATCH_TYPE_RANK[m.match_type], m.confidence) > (MATCH_TYPE_RANK[cur.match_type], cur.confidence)
        explicit = list(dict.fromkeys(cur.explicit_product_ids + m.explicit_product_ids))
        # explicitly named products first, then the rest of the supported portfolio
        products = list(dict.fromkeys(explicit + cur.product_ids + m.product_ids))
        keep, other = (m, cur) if better else (cur, m)
        keep.product_ids, keep.explicit_product_ids = products, explicit
        if keep.source != other.source and "+" not in keep.source:
            # keep the LLM's reasoning even when deterministic evidence ranks higher
            llm, lex = (keep, other) if keep.source == "LLM" else (other, keep)
            keep.explanation = f"{llm.explanation} Corroborated deterministically: {lex.explanation}".strip()
            keep.source = "LLM+LEXICON"
        merged[m.capability_id] = keep

    # 1. explicit OEM mentions
    for oh in oem_hits:
        product = catalog.products[oh.product_id]
        for cap_id in product.capabilities:
            cap = catalog.capabilities[cap_id]
            put(CapabilityMatch(
                cap_id, cap.name, cap.offering, product.name, "DIRECT", 97 if oh.in_title else 92, oh.evidence,
                f"Tender explicitly names {product.name} ({product.oem}), a supported product for {cap.name}.",
                list(dict.fromkeys([product.id] + _portfolio(catalog, cap_id))), [product.id], "LEXICON"))

    # 2. LLM requirement mapping (rules-only analyses derive requirements from the lexicon, handled in 3)
    if llm_ran:
        for req in analysis.requirements:
            if req.match_type == "NONE":
                continue
            for cap_id in req.capability_ids:
                cap = catalog.capabilities.get(cap_id)
                if cap is None:
                    continue  # never trust IDs outside the catalog
                explicit = [pid for pid in req.explicit_product_ids
                            if pid in catalog.products and cap_id in catalog.products[pid].capabilities]
                put(CapabilityMatch(
                    cap_id, cap.name, cap.offering, req.sub_capability or cap.name, req.match_type,
                    max(0, min(100, req.confidence)), req.evidence or req.text, req.explanation,
                    list(dict.fromkeys(explicit + _portfolio(catalog, cap_id))), explicit, "LLM"))

    # 3. lexicon. When the LLM has read the text, only headline (title) direct hits are added as
    #    corroboration; otherwise the lexicon is the matcher.
    for h in lexicon_hits:
        if llm_ran and not (h.in_title and h.match_type == "DIRECT"):
            continue
        cap = catalog.capabilities[h.capability_id]
        where = "title" if h.in_title else f"documents ({h.occurrences} mention{'s' if h.occurrences > 1 else ''})"
        put(CapabilityMatch(
            cap.id, cap.name, cap.offering, h.sub_capability, h.match_type, h.confidence, h.evidence,
            f"{h.match_type.title()} match: tender {where} describes “{h.sub_capability}”, which maps to the "
            f"company capability {cap.name}.", _portfolio(catalog, cap.id), [], "LEXICON"))

    return sorted(merged.values(), key=lambda m: (-MATCH_TYPE_RANK[m.match_type], -m.confidence))


def strongest(matches: list[CapabilityMatch]) -> CapabilityMatch | None:
    return matches[0] if matches else None


def relevance_gate(matches: list[CapabilityMatch], min_conf: int, generic_only: bool) -> tuple[str, str, str]:
    """Section 15. Returns (status, code, reason) with status ACCEPT | REVIEW | REJECT."""
    strong = [m for m in matches if m.match_type in ("DIRECT", "SEMANTIC") and m.confidence >= min_conf]
    if strong:
        best = strong[0]
        oem = " (explicit OEM)" if best.explicit_oem else ""
        return "ACCEPT", "STRONG_MATCH", f"{best.match_type.title()} match to {best.capability_name}{oem}: {best.sub_capability}."
    adjacent = [m for m in matches if m.match_type in ("ADJACENT", "SEMANTIC", "DIRECT")]
    if adjacent:
        m = adjacent[0]
        return "REVIEW", "ADJACENT_ONLY", (
            f"Only a weak/adjacent match ({m.match_type.lower()}, confidence {m.confidence}) to {m.capability_name}; "
            "not enough for automatic qualification.")
    if generic_only:
        return "REJECT", "GENERIC_SECURITY_ONLY", "Generic security wording only — no requirement maps to a listed capability or product."
    return "REJECT", "NO_CAPABILITY_MATCH", "No requirement maps to the company capability catalog."

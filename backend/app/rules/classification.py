"""Section 4 — SERVICE / OEM / HYBRID classification."""
from __future__ import annotations

import re

from app.analysis import Component
from app.catalog import Catalog, LexiconHit, OemHit

# A standalone, separately-delivered service attached to a product (makes it HYBRID). Plain
# installation / commissioning / warranty is treated as part of the product supply.
_HYBRID_SERVICE_RX = re.compile(
    r"managed (security )?services?|facility management|\bFMS\b|24 ?x ?7 (security )?monitoring|"
    r"SOC (operations|services|monitoring)|monitoring services|resident engineers?|onsite (manpower|resources)|"
    r"implementation (services|charges|cost)|professional services|(security )?(assessment|audit) services|"
    r"operations? (and|&) maintenance services",
    re.IGNORECASE,
)
_PRODUCT_RX = re.compile(
    r"\b(supply|SITC|procure(ment)?|purchase|licen[cs]es?|subscriptions?|appliances?|hardware|renewal|"
    r"\bATS\b|software|solution|platform)\b",
    re.IGNORECASE,
)


def heuristic_type(text: str, hits: list[LexiconHit], oems: list[OemHit], catalog: Catalog,
                   extra_capabilities: list[str] | None = None, weak_capabilities: list[str] | None = None) -> str:
    """Classification without the LLM, from which catalog offerings matched and from wording.
    `extra_capabilities` are strong non-lexicon signals (semantic matches, competitor OEM mentions)."""
    strong = [h for h in hits if h.match_type in ("DIRECT", "SEMANTIC")]
    extra = list(extra_capabilities or [])
    if not strong and not oems and not extra:
        # only adjacent evidence: let procurement wording decide
        weak = [h.capability_id for h in hits] + list(weak_capabilities or [])
        if not weak:
            return "UNKNOWN"
        # SITC = "Supply, Installation, Testing and Commissioning", standard Indian procurement shorthand
        if re.search(r"\b(supply|procure(ment)?|purchase|SITC)\b", text, re.IGNORECASE):
            return "OEM"
        offering = catalog.capabilities[weak[0]].offering
        return "OEM" if offering == "PRODUCT" else "SERVICE"
    offerings = {catalog.capabilities[h.capability_id].offering for h in strong} | \
        {catalog.capabilities[c].offering for c in extra}
    has_product = "PRODUCT" in offerings or bool(oems)
    has_service = "SERVICE" in offerings
    if has_product and (has_service or _HYBRID_SERVICE_RX.search(text)):
        return "HYBRID"
    if has_product:
        return "OEM"
    if has_service:
        # A service capability phrased as a procurement of software/licences may hide a product.
        if _PRODUCT_RX.search(text) and re.search(r"licen[cs]e|subscription|appliance|hardware", text, re.IGNORECASE):
            return "HYBRID"
        return "SERVICE"
    return "UNKNOWN"


def classify_components(components: list[Component]) -> str:
    kinds = {c.kind for c in components}
    if {"SERVICE", "PRODUCT"} <= kinds:
        return "HYBRID"
    if "PRODUCT" in kinds:
        return "OEM"
    if "SERVICE" in kinds:
        return "SERVICE"
    return "UNKNOWN"


def component_values(components: list[Component]) -> tuple[int | None, int | None, bool]:
    """(service_value, product_value, complete). `complete` is False when any component of a kind
    lacks a value — the sum for that kind is then unreliable and reported as None."""
    def total(kind: str) -> tuple[int | None, bool]:
        vals = [c.value_inr for c in components if c.kind == kind]
        if not vals:
            return None, True
        if any(v is None for v in vals):
            return None, False
        return sum(vals), True  # type: ignore[arg-type]

    sv, s_ok = total("SERVICE")
    pv, p_ok = total("PRODUCT")
    return sv, pv, s_ok and p_ok

"""Hybrid opportunity analysis and value estimation (Phase 5).

* EMD value band — GFR 2017 Rule 170 sets bid security (EMD) at 2–5% of the estimated value, so a
  stated EMD bounds the tender value: value ≈ EMD / 0.05 … EMD / 0.02. This is an *estimate*, always
  labelled as such, used only when the tender states no value; it never overrides a stated value.
* BOQ split — when a priced bill of quantities is available (XLSX/CSV, extracted to "a | b | c" rows),
  each line is classified as SERVICE or PRODUCT, giving a deterministic service/product split for the
  hybrid rules without the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.analysis import Component
from app.catalog import Catalog
from app.documents.values import parse_amount

EMD_MIN_SHARE, EMD_MAX_SHARE = 0.02, 0.05


@dataclass
class ValueBand:
    low: int
    high: int
    basis: str


def emd_value_band(emd_inr: int | None) -> ValueBand | None:
    if not emd_inr or emd_inr < 1000:
        return None
    return ValueBand(int(emd_inr / EMD_MAX_SHARE), int(emd_inr / EMD_MIN_SHARE),
                     "estimated from the EMD (GFR 2017 sets EMD at 2–5% of the estimated value)")


# ------------------------------------------------------------------ BOQ line classification
_PRODUCT_WORDS = re.compile(
    r"\b(licen[cs]es?|subscriptions?|appliances?|hardware|software|server|sensor|device|module|"
    r"perpetual|ATS|annual technical support|warranty|OEM|SKU|platform|tool|kit|workstation|equipment)\b", re.I)
_SERVICE_WORDS = re.compile(
    r"\b(implementation|installation|integration|configuration|commissioning|deployment|migration|training|"
    r"FMS|facility management|manpower|resident engineer|onsite|operations?|monitoring|managed|consult\w*|"
    r"audit|assessment|testing|VAPT|support services|services|man[- ]?months?|per day|per hour)\b", re.I)
_AMOUNT_HEADER = re.compile(r"\b(total|amount|value|cost|price)\b", re.I)
_DESC_HEADER = re.compile(r"\b(item|description|particulars|component|line item|specification|name)\b", re.I)


def classify_line(text: str, catalog: Catalog) -> str | None:
    """SERVICE / PRODUCT / None for one BOQ line, from catalog offerings and procurement vocabulary."""
    hits = catalog.lexicon_matches(text)
    offerings = {catalog.capabilities[h.capability_id].offering for h in hits if h.match_type != "ADJACENT"}
    svc, prod = len(_SERVICE_WORDS.findall(text)), len(_PRODUCT_WORDS.findall(text))
    if catalog.oem_mentions(text) or catalog.competitor_mentions(text):
        prod += 2
    if "SERVICE" in offerings:
        svc += 1
    if "PRODUCT" in offerings:
        prod += 1
    if svc == prod == 0:
        return None
    return "PRODUCT" if prod > svc else "SERVICE" if svc > prod else None


def boq_components(document_text: str, catalog: Catalog) -> list[Component]:
    """Parse priced BOQ tables in extracted spreadsheet text. Returns [] unless at least one line has
    both a classification and a positive amount (blank price-bid formats are ignored)."""
    out: list[Component] = []
    lines = [ln for ln in (document_text or "").split("\n") if ln.count("|") >= 2]
    desc_i = amt_i = None
    for ln in lines:
        cells = [c.strip() for c in ln.split("|")]
        if desc_i is None or amt_i is None:
            d = next((i for i, c in enumerate(cells) if _DESC_HEADER.search(c)), None)
            a = next((i for i, c in reversed(list(enumerate(cells))) if _AMOUNT_HEADER.search(c)), None)
            if d is not None and a is not None and d != a:
                desc_i, amt_i = d, a
            continue
        if max(desc_i, amt_i) >= len(cells):
            continue
        desc = cells[desc_i]
        raw = cells[amt_i].replace(",", "")
        try:
            amount = int(round(float(raw)))
        except ValueError:
            amount = parse_amount(cells[amt_i]) or 0
        kind = classify_line(desc, catalog)
        if kind and amount > 0 and len(desc) > 3:
            out.append(Component(desc[:200], kind, amount, f"BOQ line: {desc[:80]} = {cells[amt_i]}"))
    return out

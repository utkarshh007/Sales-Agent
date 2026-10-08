"""Deterministic INR amount parsing (Indian numbering: lakh = 1e5, crore = 1e7).

Used before any LLM call: if the value is stated plainly we never pay tokens to find it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.catalog import snippet

UNIT_MULTIPLIER = {
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "cr.": 10_000_000, "crs": 10_000_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "lakh/-": 100_000, "l": 100_000,
    "million": 1_000_000, "mn": 1_000_000, "thousand": 1_000,
}

_AMOUNT = (
    r"(?P<cur>₹|Rs\.?|INR|Rupees)?\s*(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>crores?|crs?\.?|lakhs?|lacs?|million|mn|thousand|L(?![A-Za-z]))?(?![A-Za-z0-9])"
)
AMOUNT_RX = re.compile(_AMOUNT, re.IGNORECASE)

# label -> kind. Order matters: more specific labels first.
LABELS: list[tuple[str, str]] = [
    (r"earnest money(?: deposit)?|EMD(?: amount)?|bid security", "EMD"),
    (r"tender (?:document )?fee|cost of (?:tender|bid) document", "TENDER_FEE"),
    (r"estimated (?:contract |project |tender )?(?:cost|value)|ECV|approximate (?:cost|value)|"
     r"tender value|value of (?:the )?(?:tender|work|contract)|contract value|project cost|"
     r"estimated amount|amount put to tender|budget(?:ary)? (?:estimate|provision)", "TENDER_VALUE"),
]
LABEL_RX = [(re.compile(rf"(?<![A-Za-z])(?:{p})(?![A-Za-z])", re.IGNORECASE), kind) for p, kind in LABELS]


@dataclass
class ValueCandidate:
    kind: str  # TENDER_VALUE | EMD | TENDER_FEE
    amount_inr: int
    evidence: str
    explicit_currency: bool


def parse_amount(text: str) -> int | None:
    """Parse one amount expression: 'Rs. 30,00,000', '₹2.4 Crore', '18 lakh', 'INR 1,50,00,000/-'."""
    m = AMOUNT_RX.search(text or "")
    if not m:
        return None
    return _to_inr(m)


def _to_inr(m: re.Match[str]) -> int | None:
    num = float(m.group("num").replace(",", ""))
    unit = (m.group("unit") or "").lower().rstrip(".")
    if unit == "l" and not m.group("cur"):
        return None  # a bare "L" without a currency marker is too ambiguous
    mult = UNIT_MULTIPLIER.get(unit, UNIT_MULTIPLIER.get(unit + ".", 1)) if unit else 1
    return int(round(num * mult))


def _plausible(m: re.Match[str]) -> bool:
    """Reject numbers that are probably dates, clause numbers, quantities, years..."""
    has_cur = bool(m.group("cur"))
    has_unit = bool(m.group("unit"))
    raw = m.group("num")
    if has_cur or has_unit:
        return True
    return "," in raw and len(raw.replace(",", "").split(".")[0]) >= 5


def find_value_candidates(text: str, window: int = 120) -> list[ValueCandidate]:
    out: list[ValueCandidate] = []
    if not text:
        return out
    for rx, kind in LABEL_RX:
        for lm in rx.finditer(text):
            seg = text[lm.end(): lm.end() + window]
            for am in AMOUNT_RX.finditer(seg):
                if not _plausible(am):
                    continue
                amount = _to_inr(am)
                if amount is None or amount < 1000:
                    continue
                out.append(ValueCandidate(kind, amount, snippet(text, lm.start(), lm.end() + am.end(), 40), bool(am.group("cur"))))
                break
    return out


def best_value(text: str, kind: str = "TENDER_VALUE") -> ValueCandidate | None:
    """Most frequently stated amount for a label kind (ties -> explicit currency, then first seen)."""
    cands = [c for c in find_value_candidates(text) if c.kind == kind]
    if not cands:
        return None
    counts: dict[int, int] = {}
    for c in cands:
        counts[c.amount_inr] = counts.get(c.amount_inr, 0) + 1
    return max(cands, key=lambda c: (counts[c.amount_inr], c.explicit_currency))


def format_inr(amount: int | None) -> str:
    if amount is None:
        return "UNKNOWN"
    if amount >= 10_000_000:
        return f"₹{amount / 10_000_000:.2f} Cr".replace(".00 Cr", " Cr")
    if amount >= 100_000:
        return f"₹{amount / 100_000:.2f} Lakh".replace(".00 Lakh", " Lakh")
    return f"₹{amount:,}"

"""Loads catalog.yaml and provides deterministic lexicon matching against it.

The catalog is the single source of truth for what the company sells. Both the deterministic
matcher and the LLM (which may only answer with catalog IDs) are bound to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")

MATCH_TYPE_NAMES = {"D": "DIRECT", "S": "SEMANTIC", "A": "ADJACENT"}
MATCH_TYPE_RANK = {"DIRECT": 3, "SEMANTIC": 2, "ADJACENT": 1, "NONE": 0}
LEXICON_CONFIDENCE = {"DIRECT": 90, "SEMANTIC": 75, "ADJACENT": 45}


def _compile(pattern: str, case_sensitive: bool = False) -> re.Pattern[str]:
    # Alphanumeric look-arounds instead of \b so patterns may start/end with punctuation.
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", flags)


def snippet(text: str, start: int, end: int, radius: int = 90) -> str:
    a, b = max(0, start - radius), min(len(text), end + radius)
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


@dataclass(frozen=True)
class LexRule:
    capability_id: str
    regex: re.Pattern[str]
    match_type: str  # DIRECT | SEMANTIC | ADJACENT
    sub: str
    pattern: str
    case_sensitive: bool


@dataclass
class Capability:
    id: str
    name: str
    category_code: str
    category_name: str
    offering: str  # SERVICE | PRODUCT
    rules: list[LexRule] = field(default_factory=list)
    description: str = ""

    def anchor_texts(self) -> list[str]:
        """Texts that describe this capability for semantic (embedding) matching."""
        subs = sorted({r.sub for r in self.rules if r.match_type != "ADJACENT"})
        return list(dict.fromkeys(t for t in [self.name, self.description, *subs] if t))


@dataclass
class Product:
    id: str
    name: str
    oem: str
    capabilities: list[str]
    aliases: list[str]
    alias_regexes: list[re.Pattern[str]] = field(default_factory=list)


@dataclass
class LexiconHit:
    capability_id: str
    sub_capability: str
    match_type: str
    evidence: str
    occurrences: int
    in_title: bool

    @property
    def confidence(self) -> int:
        base = LEXICON_CONFIDENCE[self.match_type]
        if self.in_title:
            base += 5
        if self.occurrences >= 3:
            base += 3
        return min(base, 99)


@dataclass
class OemHit:
    product_id: str
    evidence: str
    in_title: bool


@dataclass
class CompetitorHit:
    capability_id: str
    oem: str
    evidence: str
    in_title: bool
    or_equivalent: bool


@dataclass
class Catalog:
    version: int
    categories: dict[str, str]
    capabilities: dict[str, Capability]
    products: dict[str, Product]
    exclusions: list[tuple[re.Pattern[str], str]]
    generic_signals: list[re.Pattern[str]]
    competitors: dict[str, list[tuple[str, re.Pattern[str]]]] = field(default_factory=dict)
    negative_anchors: list[str] = field(default_factory=list)
    buyer_segments: list[tuple[str, float, re.Pattern[str]]] = field(default_factory=list)

    def buyer_segment(self, *names: str | None) -> tuple[str, float]:
        """(segment name, strategic weight) for the buying organisation; first matching rule wins."""
        text = " | ".join(n for n in names if n)
        for name, weight, rx in self.buyer_segments:
            if text and rx.search(text):
                return name, weight
        return "Other", 0.4

    # ---------------------------------------------------------------- lookups
    def products_for(self, capability_id: str) -> list[Product]:
        return [p for p in self.products.values() if capability_id in p.capabilities]

    def capability_ids(self) -> list[str]:
        return list(self.capabilities)

    def product_ids(self) -> list[str]:
        return list(self.products)

    # ---------------------------------------------------------------- matching
    def lexicon_matches(self, title: str, body: str = "") -> list[LexiconHit]:
        """Best hit per (capability, sub-capability). Title and body are scanned separately so
        we can tell a headline requirement from a passing mention deep in a document."""
        found: dict[tuple[str, str], LexiconHit] = {}
        for cap in self.capabilities.values():
            for rule in cap.rules:
                for source, text in (("title", title), ("body", body)):
                    if not text:
                        continue
                    matches = list(rule.regex.finditer(text))
                    if not matches:
                        continue
                    m = matches[0]
                    key = (cap.id, rule.sub)
                    hit = found.get(key)
                    if hit is None:
                        found[key] = LexiconHit(cap.id, rule.sub, rule.match_type,
                                                snippet(text, m.start(), m.end()), len(matches), source == "title")
                    else:
                        hit.occurrences += len(matches)
                        hit.in_title = hit.in_title or source == "title"
                        if MATCH_TYPE_RANK[rule.match_type] > MATCH_TYPE_RANK[hit.match_type]:
                            hit.match_type = rule.match_type
                            hit.evidence = snippet(text, m.start(), m.end())
        return sorted(found.values(), key=lambda h: (-MATCH_TYPE_RANK[h.match_type], -h.confidence))

    def oem_mentions(self, title: str, body: str = "") -> list[OemHit]:
        hits: list[OemHit] = []
        for p in self.products.values():
            for source, text in (("title", title), ("body", body)):
                if not text:
                    continue
                m = next((m for rx in p.alias_regexes if (m := rx.search(text))), None)
                if m:
                    hits.append(OemHit(p.id, snippet(text, m.start(), m.end()), source == "title"))
                    break
        return hits

    def competitor_mentions(self, title: str, body: str = "") -> list[CompetitorHit]:
        """OEMs the company does not sell. The mention still names a functional requirement."""
        hits: list[CompetitorHit] = []
        for cap_id, oems in self.competitors.items():
            for oem, rx in oems:
                for source, text in (("title", title), ("body", body)):
                    m = rx.search(text) if text else None
                    if m:
                        window = text[m.end(): m.end() + 40]
                        hits.append(CompetitorHit(cap_id, oem, snippet(text, m.start(), m.end()), source == "title",
                                                  bool(re.search(r"or equivalent|or similar|equivalent", window, re.I))))
                        break
        return hits

    def exclusion_hits(self, text: str) -> list[tuple[str, str]]:
        out = []
        for rx, reason in self.exclusions:
            m = rx.search(text)
            if m:
                out.append((reason, snippet(text, m.start(), m.end(), 60)))
        return out

    def has_generic_signal(self, text: str) -> bool:
        return any(rx.search(text) for rx in self.generic_signals)

    # ---------------------------------------------------------------- prompt rendering
    def as_prompt_text(self) -> str:
        """Compact, stable rendering of the catalog for the LLM system prompt (cache-friendly)."""
        lines = []
        for code, cat_name in self.categories.items():
            lines.append(f"\n## {code}. {cat_name}")
            for cap in self.capabilities.values():
                if cap.category_code != code:
                    continue
                subs = sorted({r.sub for r in cap.rules})
                prods = [p for p in self.products.values() if cap.id in p.capabilities]
                lines.append(f"- capability_id `{cap.id}` — {cap.name} [{cap.offering}]")
                if subs:
                    lines.append(f"  sub-capabilities: {'; '.join(subs)}")
                if prods:
                    lines.append("  supported products: " + "; ".join(f"`{p.id}` = {p.name} ({p.oem})" for p in prods))
        return "\n".join(lines).strip()


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    categories: dict[str, str] = data["categories"]
    capabilities: dict[str, Capability] = {}
    for c in data["capabilities"]:
        if c["category"] not in categories:
            raise ValueError(f"capability {c['id']} has unknown category {c['category']}")
        if c["offering"] not in ("SERVICE", "PRODUCT"):
            raise ValueError(f"capability {c['id']} has invalid offering {c['offering']}")
        cap = Capability(c["id"], c["name"], c["category"], categories[c["category"]], c["offering"],
                         description=(data.get("descriptions") or {}).get(c["id"], ""))
        for s in c.get("synonyms", []):
            cs = bool(s.get("cs", False))
            cap.rules.append(LexRule(cap.id, _compile(s["p"], cs), MATCH_TYPE_NAMES[s["t"]], s.get("sub", cap.name), s["p"], cs))
        if cap.id in capabilities:
            raise ValueError(f"duplicate capability id {cap.id}")
        capabilities[cap.id] = cap

    products: dict[str, Product] = {}
    for p in data["products"]:
        unknown = [cid for cid in p["capabilities"] if cid not in capabilities]
        if unknown:
            raise ValueError(f"product {p['id']} references unknown capabilities {unknown}")
        products[p["id"]] = Product(p["id"], p["name"], p["oem"], list(p["capabilities"]), list(p["aliases"]),
                                    [_compile(a) for a in p["aliases"]])

    exclusions = [(_compile(e["p"]), e["reason"]) for e in data.get("exclusions", [])]
    generic = [_compile(g) for g in data.get("generic_signals", [])]
    competitors: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for cap_id, oems in (data.get("competitors") or {}).items():
        if cap_id not in capabilities:
            raise ValueError(f"competitors references unknown capability {cap_id}")
        competitors[cap_id] = [(o, _compile(re.escape(o).replace(r"\ ", r"\s*"))) for o in oems]
    segments = [(s["name"], float(s["weight"]), re.compile(s["p"], re.IGNORECASE)) for s in data.get("buyer_segments") or []]
    return Catalog(int(data.get("version", 1)), categories, capabilities, products, exclusions, generic,
                   competitors, list(data.get("negative_anchors") or []), segments)


@lru_cache
def get_catalog() -> Catalog:
    return load_catalog()

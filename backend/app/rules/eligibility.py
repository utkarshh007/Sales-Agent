"""Eligibility feasibility (Phase 5): extract pre-qualification criteria from tender text and compare
them with the company profile.

Each criterion is reported with the tender's own wording as evidence and one of
  MET | NOT_MET | UNKNOWN (profile value missing, or criterion not machine-checkable) | RELAXED | INFO.
Nothing is assumed about the company: an empty profile field yields UNKNOWN, never MET.

Patterns were built against real Indian PSU/bank RFPs (e.g. SBI Appendix-B "Bidder's Eligibility Criteria").
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.catalog import snippet
from app.documents.values import format_inr, parse_amount

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_AMOUNT = r"(?:Rs\.?|INR|₹)\s*[\d.,]+\s*(?:crores?|cr\.?|lakhs?|lacs?)?"


def _num(token: str) -> int | None:
    token = token.strip().lower()
    return int(token) if token.isdigit() else WORD_NUM.get(token)


@dataclass
class Criterion:
    code: str
    label: str
    requirement: str  # human summary of what the tender asks
    evidence: str  # tender wording
    required: Any = None
    company: Any = None
    status: str = "UNKNOWN"  # MET | NOT_MET | UNKNOWN | RELAXED | INFO
    note: str = ""
    hard: bool = True  # NOT_MET on a hard criterion makes the bid infeasible


@dataclass
class EligibilityResult:
    assessment: str  # FEASIBLE | PARTIAL | INFEASIBLE | UNKNOWN
    criteria: list[Criterion] = field(default_factory=list)
    startup_relaxation: bool = False
    msme_relaxation: bool = False
    source: str = ""  # which text was read

    @property
    def gaps(self) -> list[Criterion]:
        return [c for c in self.criteria if c.status == "NOT_MET"]

    @property
    def unknowns(self) -> list[Criterion]:
        return [c for c in self.criteria if c.status == "UNKNOWN"]

    def to_dict(self) -> dict:
        return {"assessment": self.assessment, "startup_relaxation": self.startup_relaxation,
                "msme_relaxation": self.msme_relaxation, "source": self.source,
                "criteria": [asdict(c) for c in self.criteria]}


# ------------------------------------------------------------------ extraction (pure)
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("TURNOVER", re.compile(rf"turnover[^.;]{{0,80}}?(?:of\s+)?(?:at least|minimum|min\.?|not less than|more than|above)?\s*(?:of\s+)?(?P<amt>{_AMOUNT})", re.I | re.S)),
    ("PROFITABLE_YEARS", re.compile(rf"profit(?:able|s)?[^.;]{{0,120}}?(?:at least|minimum|for)\s+(?P<n>{_NUM})\s*(?:\(\w+\)\s*)?(?:out )?of\s+(?:the\s+)?(?:last|preceding|previous)\s+(?P<m>{_NUM})", re.I | re.S)),
    ("NET_WORTH", re.compile(rf"(?P<pos>positive net[- ]?worth)|net[- ]?worth[^.;]{{0,60}}?(?P<amt>{_AMOUNT})", re.I | re.S)),
    ("EXPERIENCE_YEARS", re.compile(rf"(?:experience of|minimum of|at least)\s*(?:minimum\s+)?(?P<n>{_NUM})\s*(?:\(\w+\)\s*)?years?|(?:minimum|at least)\s+(?P<n2>{_NUM})\s*(?:\(\w+\)\s*)?years? of (?:operations?|existence|experience)", re.I)),
    ("CLIENT_REFERENCES", re.compile(rf"at least\s+(?P<n>{_NUM})\s*(?:\(\w+\)\s*)?client references|(?P<n2>{_NUM})\s*(?:\(\w+\)\s*)?(?:similar|completed)\s+(?:works|projects|orders)", re.I)),
    ("SIMILAR_WORK_VALUE", re.compile(rf"similar (?:completed )?(?:works?|projects?|orders?)[^.;]{{0,120}}?(?:costing|value|valued at|of value)[^.;]{{0,40}}?(?:not less than|at least|minimum)?\s*(?P<amt>{_AMOUNT})", re.I | re.S)),
    ("CERT_IN", re.compile(r"CERT-?In[^.;]{0,40}?empanel|empanel+ed (?:with|by) CERT-?In", re.I)),
    ("CERTIFICATION", re.compile(r"(?P<cert>ISO[ /:-]*(?:IEC)?[ /:-]*(?:27001|9001|20000(?:-1)?|22301)|CMMI(?:[- ]?(?:DEV|SVC))?(?: level| L| ML)?[ -]?(?P<lvl>[2-5])|SOC ?2|STQC)", re.I)),
    ("LOCAL_SUPPLIER", re.compile(r"Class[- ]?I (?:or|/) Class[- ]?II local supplier|Class[- ]?I local supplier", re.I)),
    ("INDIAN_ENTITY", re.compile(r"(?:must|should|shall) be an? (?:Indian|registered) (?:Company|entity|firm)|registered under (?:the )?(?:applicable Act|Companies Act)[^.;]{0,20}India", re.I)),
    ("OEM_AUTHORISATION", re.compile(r"Manufacturer(?:'|’)?s? Authori[sz]ation(?: Form)?|\bMAF\b|authori[sz]ation (?:letter |certificate )?from (?:the )?OEM", re.I)),
    ("NOT_BLACKLISTED", re.compile(r"(?:not|never) (?:be |been )?(?:under )?(?:debar|blacklist)", re.I)),
    ("CONSORTIUM", re.compile(r"consortium (?:bidding )?(?:is )?(?:not (?:permitted|allowed)|shall not be)", re.I)),
]
_STARTUP = re.compile(r"relaxed for Start-?ups?|Start-?up[^.;]{0,80}exempt|exemption[^.;]{0,60}start-?up", re.I)
_MSME = re.compile(r"\bMSEs?\b[^.;]{0,80}(?:exempt|relax)|(?:exempt|relax)[^.;]{0,80}\bMS(?:M)?Es?\b", re.I)


_ELIG_HEADING = re.compile(r"eligibility criteria|pre[- ]?qualification (?:criteria|requirements|conditions)|"
                           r"qualifying criteria|minimum eligibility|bidder[’']?s? eligibility", re.I)
ELIGIBILITY_WINDOW = 12_000


def eligibility_text(sections: list[dict], portal_text: str = "", full_text: str = "") -> tuple[str, str]:
    """Text to read eligibility criteria from. Returns (text, description of source).

    Eligibility tables break section detection: in real RFPs a column header such as "Documents to be
    submitted" looks like a new heading and cuts the section short. So the primary source is a window
    after *every* eligibility heading in the full documents (the table of contents, the clause, the
    appendix), then identified sections, then the portal's pre-qualification note."""
    windows = [full_text[m.start(): m.start() + ELIGIBILITY_WINDOW] for m in _ELIG_HEADING.finditer(full_text or "")]
    parts = [s for s in (sec.get("eligibility") for sec in sections) if s]
    if windows or parts:
        return "\n\n".join(windows + parts), "the eligibility criteria in the tender documents"
    pq = re.search(r"^Pre-qualification: (.+)$", portal_text or "", re.M)
    if pq and not re.search(r"refer (?:to )?(?:the )?tender documents", pq.group(1), re.I):
        return pq.group(1), "the portal's pre-qualification note"
    return "", ""


def extract_criteria(text: str) -> tuple[list[Criterion], bool, bool]:
    text = re.sub(r"\s+", " ", text or "")
    found: dict[str, Criterion] = {}
    for code, rx in _RULES:
        for m in rx.finditer(text):
            # skip definitions ("“Class-I local supplier” means …"): they describe terms, not requirements
            if re.match(r"[\s”\"’']{0,3}means\b", text[m.end(): m.end() + 12], re.I):
                continue
            ev = snippet(text, m.start(), m.end(), 70)
            c = _criterion(code, m, ev)
            if c is None:
                continue
            key = f"{code}:{c.required}" if code == "CERTIFICATION" else code
            prev = found.get(key)
            # keep the most demanding numeric requirement when stated more than once; for the local-supplier
            # class, the permissive "Class-I or Class-II" clause is the actual requirement
            if prev is None or (isinstance(c.required, (int, float)) and isinstance(prev.required, (int, float))
                                and c.required > prev.required) \
                    or (code == "LOCAL_SUPPLIER" and "II" in str(c.required) and "II" not in str(prev.required)):
                found[key] = c
    return list(found.values()), bool(_STARTUP.search(text)), bool(_MSME.search(text))


def _criterion(code: str, m: re.Match[str], ev: str) -> Criterion | None:
    g = m.groupdict()
    if code == "TURNOVER":
        amt = parse_amount(g["amt"])
        return Criterion(code, "Average annual turnover", f"at least {format_inr(amt)}", ev, amt) if amt else None
    if code == "PROFITABLE_YEARS":
        n, of = _num(g["n"]), _num(g["m"])
        return Criterion(code, "Profitability", f"profitable in {n} of the last {of} years", ev, [n, of]) if n and of else None
    if code == "NET_WORTH":
        if g.get("pos"):
            return Criterion(code, "Net worth", "positive net worth", ev, 0)
        amt = parse_amount(g["amt"] or "")
        return Criterion(code, "Net worth", f"at least {format_inr(amt)}", ev, amt) if amt else None
    if code == "EXPERIENCE_YEARS":
        n = _num(g.get("n") or g.get("n2") or "")
        return Criterion(code, "Experience / years of operation", f"at least {n} years", ev, n) if n and n <= 30 else None
    if code == "CLIENT_REFERENCES":
        n = _num(g.get("n") or g.get("n2") or "")
        return Criterion(code, "Client references / similar works", f"at least {n}", ev, n) if n and n <= 20 else None
    if code == "SIMILAR_WORK_VALUE":
        amt = parse_amount(g["amt"])
        return Criterion(code, "Value of similar work", f"a similar order of at least {format_inr(amt)}", ev, amt) if amt else None
    if code == "CERT_IN":
        return Criterion(code, "CERT-In empanelment", "CERT-In empanelled", ev, True)
    if code == "CERTIFICATION":
        raw = g["cert"].upper()
        if raw.startswith("CMMI"):
            name = f"CMMI L{g.get('lvl') or 3}"
        elif "SOC" in raw:
            name = "SOC 2"
        elif "STQC" in raw:
            name = "STQC"
        else:
            name = "ISO " + re.search(r"(27001|9001|20000|22301)", raw).group(1)  # type: ignore[union-attr]
        return Criterion(code, "Certification", name, ev, name)
    if code == "LOCAL_SUPPLIER":
        need = "Class-I" if not re.search(r"Class[- ]?II", m.group(0), re.I) else "Class-I or Class-II"
        return Criterion(code, "Make in India local supplier", need, ev, need)
    if code == "INDIAN_ENTITY":
        return Criterion(code, "Indian registered entity", "registered in India", ev, True)
    if code == "OEM_AUTHORISATION":
        return Criterion(code, "OEM authorisation", "authorisation (MAF) from the OEM", ev, True)
    if code == "NOT_BLACKLISTED":
        return Criterion(code, "Not blacklisted / debarred", "no current debarment", ev, True)
    if code == "CONSORTIUM":
        return Criterion(code, "Consortium", "consortium bids not allowed", ev, None, status="INFO", hard=False,
                         note="Bid must be made alone (no consortium).")
    return None


# ------------------------------------------------------------------ assessment against the profile
RELAXABLE = {"TURNOVER", "PROFITABLE_YEARS", "EXPERIENCE_YEARS", "NET_WORTH"}


def assess(criteria: list[Criterion], profile: dict, *, startup_relaxation: bool = False,
           msme_relaxation: bool = False, matched_products: list[str] | None = None, source: str = "") -> EligibilityResult:
    p = profile or {}
    for c in criteria:
        if c.status == "INFO":
            continue
        if c.code in RELAXABLE and ((startup_relaxation and p.get("dpiit_startup")) or (msme_relaxation and p.get("msme"))):
            c.status, c.note = "RELAXED", "Relaxed for startups/MSEs, and the company profile qualifies."
            continue
        _check(c, p, matched_products or [])
    gaps = [c for c in criteria if c.status == "NOT_MET" and c.hard]
    unknown = [c for c in criteria if c.status == "UNKNOWN"]
    checked = [c for c in criteria if c.status in ("MET", "NOT_MET", "RELAXED")]
    if gaps:
        assessment = "INFEASIBLE"
    elif not criteria or not checked:
        assessment = "UNKNOWN"
    elif unknown:
        assessment = "PARTIAL"
    else:
        assessment = "FEASIBLE"
    return EligibilityResult(assessment, criteria, startup_relaxation, msme_relaxation, source)


def _check(c: Criterion, p: dict, products: list[str]) -> None:
    def status(ok: bool | None, company: Any, note: str = "") -> None:
        c.company = company
        c.status = "UNKNOWN" if ok is None else ("MET" if ok else "NOT_MET")
        c.note = note or ("Company profile has no value for this." if ok is None else "")

    if c.code == "TURNOVER":
        v = p.get("average_turnover_inr")
        status(None if v is None else v >= c.required, format_inr(v) if v is not None else None)
    elif c.code == "PROFITABLE_YEARS":
        v = p.get("profitable_years_last_3")
        status(None if v is None else v >= c.required[0], v)
    elif c.code == "NET_WORTH":
        v = p.get("net_worth_inr")
        status(None if v is None else v > c.required, format_inr(v) if v is not None else None)
    elif c.code == "EXPERIENCE_YEARS":
        v = p.get("years_in_business")
        status(None if v is None else v >= c.required, v)
    elif c.code == "CLIENT_REFERENCES":
        v = p.get("similar_projects_count")
        status(None if v is None else v >= c.required, v)
    elif c.code == "SIMILAR_WORK_VALUE":
        v = p.get("largest_similar_order_inr")
        status(None if v is None else v >= c.required, format_inr(v) if v is not None else None)
    elif c.code == "CERT_IN":
        v = p.get("empanelments")
        status(None if v is None else any("cert" in e.lower() for e in v), ", ".join(v) if v else v)
    elif c.code == "CERTIFICATION":
        v = p.get("certifications")
        if v is None:
            status(None, None)
        else:
            have = {_norm_cert(x) for x in v}
            need = _norm_cert(c.required)
            ok = need in have or (need.startswith("CMMIL") and any(h.startswith("CMMIL") and h[-1] >= need[-1] for h in have))
            status(ok, ", ".join(v))
    elif c.code == "LOCAL_SUPPLIER":
        v = p.get("local_supplier_class")
        status(None if v is None else (v == "Class-I" or (v == "Class-II" and "II" in c.required)), v)
    elif c.code == "INDIAN_ENTITY":
        v = p.get("indian_registered_entity")
        status(None if v is None else bool(v), v)
    elif c.code == "NOT_BLACKLISTED":
        v = p.get("currently_debarred")
        status(None if v is None else not v, "not debarred" if v is False else v)
    elif c.code == "OEM_AUTHORISATION":
        v = p.get("oem_authorisations")
        if v is None or not products:
            status(None, v, "Check that the OEM will issue an authorisation for this bid.")
        else:
            usable = [x for x in products if x in v]
            status(bool(usable), ", ".join(v), f"Authorised for: {', '.join(usable)}." if usable
                   else f"No authorisation on record for any matching product ({', '.join(products)}).")


def _norm_cert(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper()).replace("IEC", "")


PROFILE_FIELDS: list[tuple[str, str, str]] = [
    ("average_turnover_inr", "Average annual turnover, last 3 years (₹)", "number"),
    ("profitable_years_last_3", "Profitable years (PBT) in the last 3", "number"),
    ("net_worth_inr", "Net worth (₹)", "number"),
    ("years_in_business", "Years in business", "number"),
    ("similar_projects_count", "Similar projects completed (citable references)", "number"),
    ("largest_similar_order_inr", "Largest single similar order (₹)", "number"),
    ("certifications", "Certifications (e.g. ISO 27001, ISO 9001, CMMI L3)", "list"),
    ("empanelments", "Empanelments (e.g. CERT-In)", "list"),
    ("local_supplier_class", "Make in India local supplier class (Class-I / Class-II / Non-local)", "text"),
    ("indian_registered_entity", "Registered in India", "bool"),
    ("currently_debarred", "Currently debarred or blacklisted anywhere", "bool"),
    ("dpiit_startup", "DPIIT-recognised startup", "bool"),
    ("msme", "Registered MSE (Udyam)", "bool"),
    ("oem_authorisations", "OEMs that will issue an authorisation (product ids)", "list"),
]

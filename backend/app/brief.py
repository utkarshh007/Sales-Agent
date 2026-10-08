"""Plain-language "what they need" summary for every tender.

With the LLM on, its summary (prompted for plain words) is used. Otherwise the summary is composed from the
title and what the analysis already established. It only restates facts the tender gives: nothing is added that
the title, portal page, documents or analysis do not say.
"""
from __future__ import annotations

import re

from app.history import subject_of

# first match wins: (title pattern, what the buyer wants to do)
_ACTIONS: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), a) for p, a in [
    (r"\bselection of (?:a |the )?(?:consortium |technology |implementation )?partners?\b",
     "is looking for a partner to work with on"),
    (r"\bempanel(?:ment|ling)\b", "wants to build a panel of approved vendors for"),  # not "CERT-In empanelled"
    (r"\b(?:annual maintenance|amc|camc|o\s*&\s*m|operation and maintenance|maintenance of)\b", "wants a provider to maintain"),
    (r"\b(?:audit|vapt|assessment|penetration test|red team|review of)\b", "wants an independent"),
    (r"\b(?:training|awareness|capacity building)\b", "wants training on"),
    (r"\b(?:hiring of|managed services?|outsourcing|fms|facility management|monitoring services)\b", "wants to hire a provider for"),
    (r"\b(?:setting up|establishment of|set up|setup of)\b", "wants to set up"),
    (r"\b(?:supply|procurement|purchase|sitc|installation|commissioning|implementation|deployment|renewal|subscription)\b",
     "wants to buy"),
    (r"\b(?:consultan(?:cy|t)|advisory)\b", "wants consulting help with"),
]]

_KIND = {
    "SERVICE": "It is a services job: people and expertise, not product supply.",
    "OEM": "It is mainly a product purchase: licences, software or hardware from an OEM, usually with installation and support.",
    "HYBRID": "It mixes product supply with services such as implementation, operations or support.",
}


def _buyer(org: str | None) -> str:
    if not org:
        return "The buyer"
    org = re.sub(r"\s+", " ", org).strip()
    if org.isupper():
        short = re.search(r"[-(]\s*([A-Z&]{2,10})\)?$", org)  # "…CORPORATION OF INDIA-SPMCIL" -> "SPMCIL"
        if short:
            return short.group(1)
        org = " ".join(w if len(w) <= 4 and w not in ("OF", "AND", "FOR", "THE") else w.capitalize() for w in org.split())
        org = re.sub(r"\b(Of|And|For|The)\b", lambda m: m.group(1).lower(), org)
    m = re.search(r"\(([A-Z][A-Za-z&-]{1,10})\)", org)  # "State Bank of India (SBI)" -> keep it readable
    return org if len(org) <= 60 or not m else m.group(1)


def _subject(title: str) -> str:
    s = subject_of(title)
    if s.isupper():
        s = s.lower()
        s = re.sub(r"\b(siem|soc|ngsoc|noc|pam|dlp|vapt|ttx|eoi|rfp|it|ot|ai|cctv|waf|edr|xdr|mdr|cert-in|sbi|iso)\b",
                   lambda m: m.group(1).upper(), s)
        s = re.sub(r"\(([a-z0-9&-]{2,10})\)", lambda m: f"({m.group(1).upper()})", s)  # "(cscoe)" -> "(CSCOE)"
    s = re.sub(r"^(?:the |a )", "", s, flags=re.I).strip(" .,:;-")
    # the action verb already says this: "wants to set up" + "establishment of X" -> "X"
    s = re.sub(r"^(?:establishment|setting up|set up|setup|procurement|purchase|supply|hiring|appointment|engagement|"
               r"empanelment|selection|conducting|provision|providing)\s+of\s+(?:the\s+|an?\s+)?", "", s, flags=re.I)
    s = re.sub(r"^(?:providing|provision for)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:comprehensive\s+)?(?:annual maintenance contract|amc|camc)\s+(?:for|of)\s+(?:the\s+)?", "", s, flags=re.I)
    words = s.split()
    # lower-case a leading common word ("Tool for…"), keep names ("Privileged Access Management…", "SIEM…")
    if len(words) > 1 and words[0][:1].isupper() and words[0][1:].islower() and words[1][:1].islower():
        s = s[:1].lower() + s[1:]
    return s


_COUNTABLE = {"tool", "solution", "system", "platform", "suite", "appliance", "firewall", "partner", "vendor", "agency",
              "consultant", "provider", "firm", "laboratory", "lab", "centre", "center", "framework", "portal",
              "application", "software", "auditor"}
_HEAD_STOP = {"for", "with", "of", "including", "at", "in", "to", "and", "on"}


def _with_article(subject: str) -> str:
    """'tool for conducting TTX' -> 'a tool for conducting TTX'; plurals and uncountables are left alone."""
    words = subject.split()
    if not words or words[0].lower() in ("a", "an", "the") or words[0][:1].isdigit():
        return subject
    for w in words[:6]:
        bare = re.sub(r"[^A-Za-z]", "", w).lower()
        if bare in _HEAD_STOP:
            return subject
        if bare in _COUNTABLE:
            vowel = words[0][:1].lower() in "aeiou" and not re.match(r"(?i)u(?:s|ni|ti)|eu", words[0])
            return ("an " if vowel else "a ") + subject
    return subject


def _no_description(title: str) -> bool:
    """Titles that are only a reference: 'NIT20AERAJKOT202627', 'DEP/VSM/OT/BRC/ TRANSPORTATION/ET-3540'."""
    tokens = title.split()
    if not re.search(r"[A-Za-z]{3,}", title):
        return True
    # a code: digits mixed into a single token, or a few slash-separated fragments
    return (len(tokens) == 1 and bool(re.search(r"\d", title))) or \
        (len(tokens) <= 3 and bool(re.search(r"\d", title)) and "/" in title)


def _shorten(text: str, limit: int = 150) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:-(") + "…"


def compose(*, title: str, organization: str | None, opportunity_type: str, capability: str | None,
            products: list[str], extracted: dict | None, closing: str | None, value: str | None) -> dict:
    extracted = extracted or {}
    # the action named earliest in the title wins: "Supply … with 3 years FMS" is a purchase, not hiring
    hits = [(m.start(), i, a) for i, (rx, a) in enumerate(_ACTIONS) if (m := rx.search(title))]
    action = min(hits)[2] if hits else "needs"
    subject = _subject(title)
    if action == "wants an independent":
        if not re.search(r"\b(?:audit|assessment|test|review|auditor)\b", subject, re.I):
            subject = f"audit of {subject}"
    else:
        subject = _with_article(subject)
    if _no_description(title):
        headline = (f"{_buyer(organization)} listed this tender only as “{title.strip()}”, so the listing doesn't say "
                    "what is needed; the tender document will.")
    else:
        headline = _shorten(f"{_buyer(organization)} {action} {subject}".rstrip("."))
        headline = headline if headline.endswith("…") else headline + "."
    stage = None
    if re.search(r"\b(?:eoi|expression of interest)\b", title, re.I):
        stage = "This is an expression of interest: the first step, to shortlist vendors before a full tender."
    elif re.search(r"\b(?:rfi|request for information)\b", title, re.I):
        stage = "This is a request for information, not yet a tender."

    def known(name: str) -> str | None:
        v = (extracted.get(name) or {}).get("value") if isinstance(extracted.get(name), dict) else None
        return v if v and v != "UNKNOWN" else None

    points = []
    if (scope := known("scope_of_work")):
        points.append(("Scope", scope if len(scope) <= 280 else scope[:277].rsplit(" ", 1)[0] + "…"))
    if (dur := known("contract_duration")):
        points.append(("Contract period", dur))
    if value:
        points.append(("Stated value", value))
    if closing:
        points.append(("Bids close", closing))
    fit = None
    if capability:
        fit = f"For us this is {capability}" + (f", with products such as {', '.join(products[:3])}" if products else "") + "."
    return {"headline": headline, "stage": stage, "kind": _KIND.get(opportunity_type), "fit": fit,
            "points": [{"label": k, "value": v} for k, v in points], "source": "rules"}

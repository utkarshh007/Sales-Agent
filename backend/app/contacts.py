"""Client contact details and how to submit, read from the portal page and the tender documents.

Nothing is guessed. Every value comes with the text it was read from and its source. An email is reported as
the submission address only when the text says bids or responses go to it; Indian RFPs usually list an officer's
email for queries while bids go through an e-procurement portal, and the two must not be confused.
"""
from __future__ import annotations

import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\d/])(?:\+?91[\s-]?)?(?:[6-9]\d{4}[\s-]?\d{5}|0\d{2,4}[\s-]\d{6,8})(?![\d/])")
URL = re.compile(r"https?://[^\s|,;)\]]+", re.I)

# portal-wide helpdesk addresses are not the buyer's contact
_PORTAL_HELPDESK = re.compile(r"^(?:support-eproc|cppp-nic|eproc|helpdesk|support|noreply|no-reply)[\w.-]*@", re.I)

def _spaced(pattern: str) -> re.Pattern[str]:
    """Words in table cells come out of PDFs split over lines: let every space match any whitespace."""
    return re.compile(pattern.replace(" ", r"\s+"), re.I)


_LABEL = re.compile(
    r"(?im)^\s*(?P<label>name(?: of (?:the )?(?:officer|contact person))?|contact person|designation|"
    r"e-?mail(?:\s*(?:id|address))?|(?:contact |office |postal |correspondence )?address|"
    r"(?:contact|mobile|phone|telephone|tel)(?:\.?\s*(?:no\.?|number))?)\s*[:\-–]\s*")
_ROW_END = re.compile(r"\n\s*\d{1,2}\s*\n|\n\s*\n|\[page \d+\]")

_CLIENT_BLOCK = _spaced(
    r"contact details of (?:the )?(?:issuing|tender inviting|procuring) (?:department|authority|office)|"
    r"(?:officer|person) to be contacted|for (?:any )?(?:queries|clarifications?)[^\n]{0,40}contact|"
    r"contact person|tender inviting authority|correspondence regarding this")
_AGENCY_BLOCK = _spaced(r"contact details of e-?\s*procurement agency|e-?procurement agency appointed|"
                           r"help\s*desk|helpdesk")
_SUBMIT_BLOCK = _spaced(r"address for submission of (?:the )?(?:bids?|proposals?|offers?|responses?)|"
                           r"(?:mode|place|manner) of (?:bid )?submission|bid submission (?:mode|address)")
_EMAIL_SUBMIT = re.compile(
    r"(?:bids?|proposals?|responses?|eois?|quotations?|offers?|applications?|documents)\s+(?:\w+\s+){0,6}?"
    r"(?:should|shall|must|may|are to|is to|to)\s+be\s+(?:sent|submitted|e-?mailed|forwarded|mailed)\b"
    r"[^.]{0,120}?(?:e-?mail|mail)[^.]{0,60}?(?P<email>" + EMAIL.pattern + ")|"
    r"(?:submit|send|e-?mail|forward)\s+(?:\w+\s+){0,5}?(?:bids?|proposals?|responses?|eois?|quotations?|offers?)"
    r"[^.]{0,80}?(?:to|at|on)\s+(?P<email2>" + EMAIL.pattern + ")", re.I)
_PHYSICAL = re.compile(r"sealed (?:cover|envelope)|hard cop(?:y|ies)|physical(?:ly)? submi|by (?:hand|post|courier)", re.I)

# where bids go on portals whose submission is online by design
PORTAL_SUBMISSION = {
    "cppp": ("Central Public Procurement Portal", "https://eprocure.gov.in/eprocure/app"),
}


def _clean(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip(" ,;|-–")


def _labelled(block: str) -> dict:
    """'Name: X / Designation: Y / Email ID: z / Contact Address: … / Contact Number: …' blocks."""
    out: dict[str, str] = {}
    marks = list(_LABEL.finditer(block))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        value = block[m.end():end]
        cut = _ROW_END.search(value)
        value = _clean(value[:cut.start()] if cut else value)
        if not value:
            continue
        label = m.group("label").lower()
        if label.startswith(("name", "contact person")):
            key = "name"
        elif label.startswith("designation"):
            key = "designation"
        elif label.startswith("e"):
            key = "email"
        elif "address" in label:
            key = "address"
        else:
            key = "phone"
        if key == "email":
            em = EMAIL.search(value)
            value = em.group(0) if em else ""
        elif key == "phone":
            ph = PHONE.search(value)
            value = _clean(ph.group(0)) if ph else ""
        elif key in ("name", "designation"):
            value = value[:120]
        else:
            value = value[:300]
        if value and key not in out:
            out[key] = value
    return out


def _find_block(rx: re.Pattern[str], text: str, size: int = 700) -> tuple[str, str] | None:
    m = rx.search(text)
    return (text[m.start():m.start() + size], _clean(text[m.start():m.start() + 260])) if m else None


def extract(*, portal_code: str | None, portal_name: str | None, portal_url: str | None, organization: str | None,
            department: str | None, portal_contact: str | None, texts: list[tuple[str, str]]) -> dict:
    """texts: [(source name, text)] — the portal detail text first, then each document."""
    client: dict = {"organization": organization, "department": department}
    submission: dict = {"mode": "UNKNOWN"}
    helpdesk: dict = {}
    used: set[str] = set()

    if portal_contact:  # GePNIC "Tender Inviting Authority": "Name — Address"
        name, _, address = portal_contact.partition(" — ")
        client.update({"name": _clean(name) or None, "address": _clean(address) or None,
                       "source": "portal tender page", "evidence": portal_contact})

    for source, text in texts:
        if not text:
            continue
        if not client.get("email"):
            found = _find_block(_CLIENT_BLOCK, text)
            if found:
                fields = _labelled(found[0])
                if not fields.get("email"):
                    em = EMAIL.search(found[0])
                    if em and not _PORTAL_HELPDESK.match(em.group(0)):
                        fields["email"] = em.group(0)
                if fields:
                    for k, v in fields.items():
                        client.setdefault(k, None)
                        client[k] = client[k] or v
                    client["source"], client["evidence"] = source, found[1]
        if not helpdesk:
            found = _find_block(_AGENCY_BLOCK, text, 450)
            if found:
                block = found[0]
                em, ph = EMAIL.search(block), PHONE.search(block)
                if em or ph:
                    helpdesk = {"email": em.group(0) if em else None, "phone": _clean(ph.group(0)) if ph else None,
                                "source": source, "evidence": found[1]}
        if submission["mode"] == "UNKNOWN" or not submission.get("email"):
            m = _EMAIL_SUBMIT.search(text)
            if m:
                email = m.group("email") or m.group("email2")
                submission = {"mode": "EMAIL", "email": email, "source": source,
                              "evidence": _clean(text[max(0, m.start() - 40):m.end() + 40])}
            elif submission["mode"] == "UNKNOWN":
                found = _find_block(_SUBMIT_BLOCK, text, 400)
                if found:
                    block = found[0]
                    url = URL.search(block)
                    if url or re.search(r"\bonline\b|e-?procurement portal|e-?tender", block, re.I):
                        submission = {"mode": "ONLINE_PORTAL", "url": url.group(0).rstrip(".") if url else None,
                                      "source": source, "evidence": found[1]}
                    elif _PHYSICAL.search(block):
                        submission = {"mode": "PHYSICAL", "address": _clean(block.split("\n", 1)[-1])[:300],
                                      "source": source, "evidence": found[1]}

    if submission["mode"] == "UNKNOWN":
        if portal_code in PORTAL_SUBMISSION:
            name, url = PORTAL_SUBMISSION[portal_code]
            submission = {"mode": "ONLINE_PORTAL", "url": url, "portal_name": name, "source": "portal",
                          "evidence": f"Tenders published on {name} are submitted online on that portal."}
        elif portal_code and portal_code.startswith("gepnic") and portal_url:
            submission = {"mode": "ONLINE_PORTAL", "url": portal_url, "portal_name": portal_name, "source": "portal",
                          "evidence": f"Tenders published on {portal_name} (NIC GePNIC) are submitted online on that portal."}
    if submission["mode"] == "UNKNOWN":  # last resort, weaker: a mention of sealed covers or hard copies anywhere
        for source, text in texts:
            m2 = _PHYSICAL.search(text or "")
            if m2:
                submission = {"mode": "PHYSICAL", "source": source,
                              "evidence": _clean(text[max(0, m2.start() - 120):m2.end() + 160])}
                break

    for k in ("email",):
        if client.get(k):
            used.add(client[k].lower())
    if helpdesk.get("email"):
        used.add(helpdesk["email"].lower())
    if submission.get("email"):
        used.add(submission["email"].lower())

    other: list[dict] = []
    for source, text in texts:
        for m in EMAIL.finditer(text or ""):
            e = m.group(0)
            if e.lower() in used or _PORTAL_HELPDESK.match(e) or any(o["email"].lower() == e.lower() for o in other):
                continue
            other.append({"email": e, "source": source, "context": _clean(text[max(0, m.start() - 140):m.end() + 40])})
            if len(other) >= 6:
                break

    has_client = any(client.get(k) for k in ("name", "email", "phone", "address"))
    return {"client": client if has_client or organization else None, "submission": submission,
            "helpdesk": helpdesk or None, "other_emails": other,
            "read_documents": [s for s, t in texts if t and s != "portal tender page"]}

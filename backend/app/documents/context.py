"""Targeted LLM context (section 9 step 10): send the sections that matter, not whole documents."""
from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_PRIORITY = [
    "scope_of_work", "technical_requirements", "commercial", "eligibility", "deliverables",
    "support_sla", "manpower", "emd_fees", "important_dates", "mandatory_documents",
]
KEYWORD_RX = re.compile(
    r"estimated (cost|value)|tender value|contract value|earnest money|\bEMD\b|turnover|experience|"
    r"OEM|make and model|licen[cs]e|subscription|quantity|period of contract|duration|"
    r"SIEM|SOC|PAM|DLP|XDR|EDR|VAPT|penetration|forensic|audit|cloud|firewall|ISO ?27001|PCI|CERT-?In",
    re.IGNORECASE,
)


@dataclass
class DocText:
    filename: str
    text: str
    sections: dict[str, str]


def build_llm_context(docs: list[DocText], max_chars: int) -> tuple[str, bool]:
    """Returns (context, truncated). Small document sets are sent whole; large ones are reduced to
    prioritised sections, then keyword windows, until the budget is used."""
    full = "\n\n".join(f"=== DOCUMENT: {d.filename} ===\n{d.text}" for d in docs if d.text)
    if len(full) <= max_chars:
        return full, False

    parts: list[str] = []
    used = 0

    def add(block: str) -> bool:
        nonlocal used
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 2000:
                parts.append(block[:remaining] + "\n[…section truncated…]")
                used = max_chars
            return False
        parts.append(block)
        used += len(block)
        return True

    # 1. opening of every document (title page / NIT summary usually carries value, dates, EMD)
    for d in docs:
        add(f"=== DOCUMENT: {d.filename} — opening ===\n{d.text[:4000]}")
    # 2. identified sections in priority order, round-robin across documents
    for name in SECTION_PRIORITY:
        for d in docs:
            body = d.sections.get(name)
            if body and not add(f"=== DOCUMENT: {d.filename} — section: {name} ===\n{body}"):
                return "\n\n".join(parts), True
    # 3. keyword windows for documents without recognisable headings
    for d in docs:
        if d.sections:
            continue
        seen_until = 0
        for m in KEYWORD_RX.finditer(d.text):
            if m.start() < seen_until:
                continue
            a, b = max(0, m.start() - 600), min(len(d.text), m.end() + 600)
            seen_until = b
            if not add(f"=== DOCUMENT: {d.filename} — excerpt ===\n{d.text[a:b]}"):
                return "\n\n".join(parts), True
    return "\n\n".join(parts), True

"""Section 17/18 — email alerts. Tender text is untrusted, so everything is HTML-escaped. Secrets and
credentials are never included in emails."""
from __future__ import annotations

import html
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import Catalog
from app.config import Settings
from app.db import as_utc, utcnow
from app.documents.values import format_inr
from app.models import Alert, Tender

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

SHORT_CAPABILITY = {
    "prd_siem": "SIEM", "prd_pam": "PAM", "prd_dlp": "DLP", "prd_xdr": "XDR", "prd_cspm": "Cloud Security",
    "prd_bas": "Breach & Attack Simulation", "prd_iam": "IAM", "prd_vuln_mgmt": "Vulnerability Management",
    "prd_appsec_tools": "AppSec Tooling", "prd_ot_security": "OT Security", "prd_sase": "SASE",
    "prd_threat_intel": "Threat Intelligence", "mss_mdr": "Managed SOC / MDR",
}


def _product_label(catalog: Catalog, pid: str) -> str:
    p = catalog.products.get(pid)
    if p is None:
        return pid
    alias = p.aliases[0] if p.aliases else p.name
    return alias if re.fullmatch(r"[\w .&-]+", alias) else p.name


def _ist(dt: datetime | None) -> str:
    dt = as_utc(dt)
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if dt else "UNKNOWN"


def render_tender_alert(t: Tender, settings: Settings, catalog: Catalog) -> tuple[str, str, str]:
    cap_id = (t.matched_capabilities or [None])[0]
    cap_name = SHORT_CAPABILITY.get(cap_id or "", t.primary_capability or "Cybersecurity")
    value = t.value_analysis.get("total_value_inr") if t.value_analysis else None
    value_txt = format_inr(value) if value else "Value TBD"
    products = [_product_label(catalog, p) for p in (t.matched_products or [])][:3]
    review = " [REVIEW]" if t.decision == "MANUAL_REVIEW" else ""
    if t.opportunity_type == "SERVICE":
        subject = f"[{t.priority}]{review} {value_txt} {t.primary_capability or cap_name} — Service Match"
    else:
        kind = "Hybrid " if t.opportunity_type == "HYBRID" else ""
        subject = f"[{t.priority}]{review} {value_txt} {cap_name} {kind}Opportunity — {'/'.join(products) or cap_name} Match"

    cap = settings.SERVICE_MAX_VALUE_INR
    if t.opportunity_type == "SERVICE":
        value_rule = (f"Service opportunity at {format_inr(value)} — within the {format_inr(cap)} service limit."
                      if value else f"Service opportunity; value not yet determined — confirm it is ≤ {format_inr(cap)}.")
    elif t.opportunity_type == "OEM":
        value_rule = f"OEM/product opportunity — the {format_inr(cap)} limit applies only to services, so the value does not disqualify it."
    else:
        sv = t.service_value_inr
        value_rule = (f"Hybrid: product component is not capped; service component "
                      f"{format_inr(sv) if sv else 'not separately stated'} (service limit {format_inr(cap)}).")

    ex = t.extracted or {}
    reqs = [r["text"] for r in ex.get("_requirements", []) if r.get("match_type") in ("DIRECT", "SEMANTIC")][:6]
    if not reqs and ex.get("scope_of_work", {}).get("value", "UNKNOWN") != "UNKNOWN":
        reqs = [ex["scope_of_work"]["value"][:400]]
    issues = (ex.get("_eligibility", {}) or {}).get("issues", []) + ex.get("_risks", [])
    if "NO_DOCUMENTS" in (t.flags or []):
        issues.append("Tender documents not yet analysed — " + (t.blocker_note or "upload them on the dashboard."))
    oem_line = ", ".join(catalog.products[p].name for p in (t.matched_products or []) if p in catalog.products) or "—"
    link = f"{settings.DASHBOARD_BASE_URL.rstrip('/')}/tenders/{t.id}"

    rows = [
        ("Tender", t.title), ("Organisation", t.organization or "UNKNOWN"),
        ("Reference", " / ".join(x for x in (t.reference_number, t.portal_tender_id) if x) or "UNKNOWN"),
        ("Closing date", _ist(t.closing_at)), ("Tender value", value_txt), ("Classification", t.opportunity_type),
        ("Matched capability", t.primary_capability or "—"), ("Matching OEMs / products", oem_line),
        ("Score", f"{t.score} ({t.priority})"), ("Decision", t.decision),
    ]
    text = "\n".join(f"{k}: {v}" for k, v in rows)
    text += f"\n\nWhy it matches:\n{t.relevance_reason or '—'}\n\nValue rule:\n{value_rule}\n"
    if reqs:
        text += "\nKey requirements:\n" + "\n".join(f"- {r}" for r in reqs) + "\n"
    if issues:
        text += "\nRisks / eligibility issues:\n" + "\n".join(f"- {i}" for i in issues[:8]) + "\n"
    text += f"\nSource: {t.source_url or '—'}\nTrever RFP Portal: {link}\n"

    e = html.escape
    html_body = (
        "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111'>"
        f"<h2 style='margin:0 0 8px'>{e(subject)}</h2><table cellpadding='4' style='border-collapse:collapse'>"
        + "".join(f"<tr><td style='color:#555;vertical-align:top'>{e(k)}</td><td><b>{e(str(v))}</b></td></tr>" for k, v in rows)
        + "</table>"
        f"<h3>Why it matches</h3><p>{e(t.relevance_reason or '—')}</p>"
        f"<h3>Value rule</h3><p>{e(value_rule)}</p>"
        + (("<h3>Key requirements</h3><ul>" + "".join(f"<li>{e(r)}</li>" for r in reqs) + "</ul>") if reqs else "")
        + (("<h3>Risks / eligibility issues</h3><ul>" + "".join(f"<li>{e(i)}</li>" for i in issues[:8]) + "</ul>") if issues else "")
        + f"<p><a href='{e(t.source_url or '#')}'>Source tender</a> · <a href='{e(link)}'>Open in Trever RFP Portal</a></p>"
        "<p style='color:#777;font-size:12px'>Sent by Trever RFP Portal.</p></div>"
    )
    return subject, text, html_body


def send_email(settings: Settings, recipients: list[str], subject: str, text: str, html_body: str | None) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.ALERT_FROM, ", ".join(recipients)
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(msg)


def _deliver(session: Session, alert: Alert, settings: Settings) -> Alert:
    if not settings.SMTP_HOST or not alert.recipients:
        alert.status, alert.error = "NOT_CONFIGURED", "SMTP_HOST / ALERT_RECIPIENTS not set; alert stored only"
    else:
        try:
            send_email(settings, alert.recipients, alert.subject, alert.body_text, alert.body_html)
            alert.status, alert.sent_at = "SENT", utcnow()
        except Exception as e:  # stored with error; job layer decides on retry
            alert.status, alert.error = "FAILED", f"{type(e).__name__}: {e}"
    session.commit()
    if alert.status == "FAILED":
        raise RuntimeError(alert.error)
    return alert


def send_tender_alert(session: Session, tender_id: int, version: int, settings: Settings, catalog: Catalog) -> Alert | None:
    key = f"immediate:{tender_id}:v{version}"
    alert = session.scalar(select(Alert).where(Alert.dedupe_key == key))
    if alert is not None and alert.status in ("SENT", "NOT_CONFIGURED"):
        return alert
    t = session.get(Tender, tender_id)
    if t is None:
        return None
    if alert is None:
        subject, text, html_body = render_tender_alert(t, settings, catalog)
        alert = Alert(tender_id=t.id, tender_version=version, kind="IMMEDIATE", priority=t.priority, dedupe_key=key,
                      recipients=settings.alert_recipients, subject=subject, body_text=text, body_html=html_body)
        session.add(alert)
        session.flush()
    return _deliver(session, alert, settings)


def send_digest(session: Session, settings: Settings, catalog: Catalog, now: datetime | None = None) -> Alert | None:
    now = now or utcnow()
    key = f"digest:{now.astimezone(IST):%Y-%m-%d}"
    if session.scalar(select(Alert).where(Alert.dedupe_key == key)) is not None:
        return None
    last = session.scalar(select(Alert.created_at).where(Alert.kind == "DIGEST").order_by(Alert.created_at.desc()).limit(1))
    since = as_utc(last) or (now - timedelta(days=1))
    priorities = ["MEDIUM"] + (["HIGH"] if settings.HIGH_ALERT_MODE == "digest" else [])
    tenders = session.scalars(select(Tender).where(
        Tender.decision.in_(("ACCEPTED", "MANUAL_REVIEW")), Tender.priority.in_(priorities),
        Tender.last_analyzed_at >= since).order_by(Tender.score.desc())).all()
    if not tenders:
        return None
    lines, items = [], []
    for t in tenders:
        subject, _, _ = render_tender_alert(t, settings, catalog)
        link = f"{settings.DASHBOARD_BASE_URL.rstrip('/')}/tenders/{t.id}"
        lines.append(f"- {subject}\n  {t.organization or ''} · closes {_ist(t.closing_at)}\n  {link}")
        items.append(f"<li><a href='{html.escape(link)}'>{html.escape(subject)}</a><br>"
                     f"<span style='color:#555'>{html.escape(t.organization or '')} · closes {html.escape(_ist(t.closing_at))}</span></li>")
    subject = f"Tender digest — {len(tenders)} opportunit{'y' if len(tenders) == 1 else 'ies'} ({now.astimezone(IST):%d %b %Y})"
    alert = Alert(kind="DIGEST", priority="MEDIUM", dedupe_key=key, recipients=settings.alert_recipients, subject=subject,
                  body_text="\n\n".join(lines),
                  body_html="<div style='font-family:Segoe UI,Arial,sans-serif'><h2>" + html.escape(subject) + "</h2><ul>"
                            + "".join(items) + "</ul></div>")
    session.add(alert)
    session.flush()
    return _deliver(session, alert, settings)

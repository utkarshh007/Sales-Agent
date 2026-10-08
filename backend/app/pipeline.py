"""DB-backed pipeline steps (section 22):

  scheduler -> discover_portal -> upsert_listing -> [process_documents] -> analyze_tender -> alerts

Each step is idempotent and runs as a job, so a crash or retry never double-processes a tender.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit, jobs
from app.analysis import TenderContext
from app.catalog import Catalog
from app.config import Settings
from app.connectors.base import DocumentRef, HumanInterventionRequired, PortalAccessDenied, PortalConnector, TenderListing
from app.connectors.registry import DEFAULT_PORTALS, build_connector
from app.db import as_utc, utcnow
from app.documents.context import DocText, build_llm_context
from app.documents.processor import flatten, process_bytes
from app.engine import ACCEPTED, MANUAL_REVIEW, Decision, evaluate
from app.llm.analyzer import Analyzer
from app.models import (
    CapabilityRow, CapabilitySynonym, ExtractedRequirement, ManualReview, Oem, Portal, ProductRow, RejectionReason,
    ScoreRow, Tender, TenderDocument, TenderMatch, TenderSource, TenderVersion,
)

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ reference data
def seed_reference_data(session: Session, catalog: Catalog) -> None:
    """Mirror catalog.yaml into the catalog tables and make sure default portals exist."""
    session.execute(delete(CapabilitySynonym))
    session.execute(delete(ProductRow))
    session.flush()
    existing = {c.id: c for c in session.scalars(select(CapabilityRow))}
    for cap in catalog.capabilities.values():
        row = existing.get(cap.id) or CapabilityRow(id=cap.id)
        row.name, row.category_code, row.category_name, row.offering = cap.name, cap.category_code, cap.category_name, cap.offering
        session.add(row)
        for r in cap.rules:
            session.add(CapabilitySynonym(capability_id=cap.id, pattern=r.pattern, match_type=r.match_type,
                                          sub_capability=r.sub, case_sensitive=r.case_sensitive))
    for stale in set(existing) - set(catalog.capabilities):
        session.delete(existing[stale])
    oems = {o.name: o for o in session.scalars(select(Oem))}
    for p in catalog.products.values():
        oem = oems.get(p.oem)
        if oem is None:
            oem = Oem(name=p.oem)
            session.add(oem)
            session.flush()
            oems[p.oem] = oem
        session.add(ProductRow(id=p.id, name=p.name, oem_id=oem.id, capabilities=p.capabilities, aliases=p.aliases))
    for spec in DEFAULT_PORTALS:
        if session.scalar(select(Portal).where(Portal.code == spec["code"])) is None:
            session.add(Portal(**spec))
    session.commit()


# ------------------------------------------------------------------ discovery
def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


LISTING_FIELDS = ("title", "reference_number", "organization", "department", "location", "published_at",
                  "closing_at", "opening_at", "corrigendum", "contact_info")
DETAIL_FIELDS = ("tender_value_inr", "emd_inr", "category", "tender_type", "location", "contact_info")


def _snapshot(t: Tender) -> dict:
    return json.loads(json.dumps({
        "title": t.title, "reference_number": t.reference_number, "closing_at": t.closing_at,
        "opening_at": t.opening_at, "corrigendum": t.corrigendum, "tender_value_inr": t.tender_value_inr,
        "emd_inr": t.emd_inr, "category": t.category, "portal_text": (t.raw or {}).get("portal_text"),
    }, default=str))


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def find_cross_portal_match(session: Session, portal: Portal, item: TenderListing) -> tuple[Tender, str] | None:
    """The same tender is often listed on several portals (CPPP aggregates GePNIC instances). Tender
    IDs are only unique per portal, so an ID match must be confirmed by reference number or title."""
    if not item.portal_tender_id:
        return None
    for t in session.scalars(select(Tender).where(Tender.portal_tender_id == item.portal_tender_id)):
        if any(src.portal_id == portal.id for src in t.sources):
            continue  # same portal => genuinely different tender
        if item.reference_number and _norm(t.reference_number) == _norm(item.reference_number):
            return t, "TENDER_ID+REF"
        a, b = _norm(t.title), _norm(item.title)
        if a and b and (a == b or (min(len(a), len(b)) >= 25 and (a.startswith(b) or b.startswith(a)))):
            return t, "TENDER_ID+TITLE"
    return None


def _apply(t: Tender, item: TenderListing, *, listing_changed: bool, detail_changed: bool, primary: bool) -> list[str]:
    """Copy fields from a listing onto the tender; returns the names of fields that changed."""
    changed: list[str] = []

    def put(field: str, value) -> None:
        if value is not None and getattr(t, field) != value:
            changed.append(field)
            setattr(t, field, value)

    if listing_changed:
        for f in LISTING_FIELDS:
            val = getattr(item, f)
            if f == "title" and not primary and t.title:
                continue  # keep the primary source's wording
            if f == "corrigendum" and primary and val is None and t.corrigendum:
                changed.append(f)
                t.corrigendum = None
                continue
            put(f, val)
    if detail_changed:
        for f in DETAIL_FIELDS:
            put(f, getattr(item, f))
        raw = dict(t.raw or {})
        if item.portal_text and raw.get("portal_text") != item.portal_text:
            raw["portal_text"] = item.portal_text
            changed.append("portal_text")
        if item.raw.get("document_names"):
            raw["document_names"] = item.raw["document_names"]
        t.raw = raw
    return changed


def upsert_listing(session: Session, portal: Portal, connector: PortalConnector, item: TenderListing) -> tuple[Tender, str]:
    """Returns (tender, NEW | MERGED | UPDATED | UNCHANGED). Material changes create a tender_version
    and queue re-analysis; unchanged sightings only refresh last_seen_at."""
    fp = connector.fingerprint(item)
    lhash = _hash(item.content_signature())
    dsig = item.detail_signature()
    dhash = _hash(dsig) if dsig is not None else None
    src = session.scalar(select(TenderSource).where(TenderSource.fingerprint == fp))
    now = utcnow()

    if src is not None:
        t = src.tender
        listing_changed = src.listing_hash != lhash
        detail_changed = dhash is not None and src.detail_hash != dhash
        src.last_seen_at = now
        if not listing_changed and not detail_changed:
            return t, "UNCHANGED"
        src.listing_hash = lhash
        src.detail_hash = dhash or src.detail_hash
        src.lookup_hint = item.lookup_hint or src.lookup_hint
        changed = _apply(t, item, listing_changed=listing_changed, detail_changed=detail_changed,
                         primary=t.portal_id == portal.id)
        if not changed:
            return t, "UNCHANGED"
        status, change = "UPDATED", f"{portal.code} changed: " + ", ".join(dict.fromkeys(changed))
    else:
        match = find_cross_portal_match(session, portal, item)
        if match is not None:
            t, basis = match
            changed = _apply(t, item, listing_changed=True, detail_changed=dhash is not None, primary=False)
            t.sources.append(TenderSource(portal_id=portal.id, fingerprint=fp, portal_tender_id=item.portal_tender_id,
                                          source_url=item.source_url, lookup_hint=item.lookup_hint, listing_hash=lhash,
                                          detail_hash=dhash, match_basis=basis))
            if not changed:
                audit.record(session, "TENDER_MERGED", tender_id=t.id, details={"portal": portal.code, "basis": basis})
                session.flush()
                return t, "MERGED"
            status, change = "MERGED", f"also listed on {portal.code} ({basis}); added: " + ", ".join(dict.fromkeys(changed))
        else:
            t = Tender(portal_id=portal.id, fingerprint=fp, content_hash=lhash, version=0, title=item.title,
                       source_url=item.source_url, portal_tender_id=item.portal_tender_id, raw=dict(item.raw))
            session.add(t)
            _apply(t, item, listing_changed=True, detail_changed=dhash is not None, primary=True)
            t.sources.append(TenderSource(portal_id=portal.id, fingerprint=fp, portal_tender_id=item.portal_tender_id,
                                          source_url=item.source_url, lookup_hint=item.lookup_hint, listing_hash=lhash,
                                          detail_hash=dhash, match_basis="PRIMARY"))
            session.flush()
            status, change = "NEW", f"first seen on {portal.code}"

    t.version += 1
    snap = _snapshot(t)
    t.content_hash = _hash(snap)
    session.add(TenderVersion(tender_id=t.id, version=t.version, content_hash=t.content_hash, snapshot=snap,
                              change_summary=change))

    known_urls = {d.source_url for d in t.documents}
    for ref in item.documents:
        if ref.url not in known_urls:
            session.add(TenderDocument(tender_id=t.id, filename=ref.filename, source_url=ref.url, origin="PORTAL",
                                       status="PENDING_DOWNLOAD"))
    if item.documents:
        t.documents_status, t.blocker_note = "PENDING", None
    elif item.documents_blocker and t.documents_status in ("NONE", "BLOCKED_HUMAN_REQUIRED"):
        t.documents_status, t.blocker_note = "BLOCKED_HUMAN_REQUIRED", item.documents_blocker
    t.pipeline_status = "QUEUED"
    audit.record(session, "TENDER_" + status, tender_id=t.id,
                 details={"version": t.version, "change": change, "portal": portal.code, "source_url": item.source_url})
    session.flush()
    if item.documents:
        jobs.enqueue(session, jobs.PROCESS_DOCUMENTS, {"tender_id": t.id}, dedupe_key=f"docs:{t.id}")
    else:
        jobs.enqueue(session, jobs.ANALYZE_TENDER, {"tender_id": t.id}, dedupe_key=f"analyze:{t.id}")
    return t, status


def discover_portal(session: Session, portal: Portal, settings: Settings, connector: PortalConnector | None = None,
                    catalog: Catalog | None = None) -> dict:
    from app.catalog import get_catalog
    from app.rules.prefilter import title_is_candidate

    catalog = catalog or get_catalog()
    connector = connector or build_connector(portal.connector, portal.code, portal.config, settings)
    known = {fp: (lh, dh) for fp, lh, dh in session.execute(
        select(TenderSource.fingerprint, TenderSource.listing_hash, TenderSource.detail_hash)
        .where(TenderSource.portal_id == portal.id))}

    def wants_detail(item: TenderListing) -> bool:
        """Read a detail page only for plausible matches that are new, changed or never detailed."""
        prev = known.get(connector.fingerprint(item))
        if prev is not None and prev[0] == _hash(item.content_signature()) and prev[1] is not None:
            return False
        return title_is_candidate(item.title, item.closing_at, catalog)

    result = connector.discover(lambda fp: fp in known, wants_detail)
    counts = {"NEW": 0, "MERGED": 0, "UPDATED": 0, "UNCHANGED": 0}
    for item in result.items:
        try:
            with session.begin_nested():  # a bad row rolls back alone, not the whole run
                _, status = upsert_listing(session, portal, connector, item)
            counts[status] += 1
        except Exception as e:
            log.exception("upsert failed")
            result.errors.append(f"upsert {item.identity_key()}: {type(e).__name__}: {e}")
    portal.last_run_at = utcnow()
    portal.last_status = "ERROR" if result.errors and not result.items else (
        "BLOCKED" if result.blockers and not result.items else ("PARTIAL" if result.errors or result.blockers else "OK"))
    portal.last_error = "\n".join(result.errors[:20] + result.blockers[:20])[:4000] or None
    summary = {"pages": result.pages_fetched, **counts, "blockers": result.blockers[:50], "errors": result.errors[:50],
               "stopped": result.stopped_reason}
    audit.record(session, "DISCOVERY_RUN", details={"portal": portal.code, **summary})
    session.commit()
    return summary


# ------------------------------------------------------------------ documents
def _storage_path(settings: Settings, tender_id: int, sha: str, filename: str) -> Path:
    ext = Path(filename).suffix.lower()[:10] if Path(filename).suffix else ""
    d = Path(settings.DOCUMENT_STORAGE_DIR) / str(tender_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sha}{ext}"  # content-addressed name; never the user-supplied filename


def store_upload(session: Session, tender: Tender, filename: str, data: bytes, settings: Settings, user: str) -> TenderDocument:
    sha = hashlib.sha256(data).hexdigest()
    dup = session.scalar(select(TenderDocument).where(TenderDocument.tender_id == tender.id, TenderDocument.sha256 == sha))
    if dup is not None:
        return dup
    path = _storage_path(settings, tender.id, sha, filename)
    path.write_bytes(data)
    doc = TenderDocument(tender_id=tender.id, filename=Path(filename).name[:300], origin="UPLOAD", size_bytes=len(data),
                         sha256=sha, storage_path=str(path), status="PENDING", uploaded_by=user)
    session.add(doc)
    tender.documents_status = "PENDING"
    audit.record(session, "DOCUMENT_UPLOADED", tender_id=tender.id, actor=user,
                 details={"filename": doc.filename, "sha256": sha, "bytes": len(data)})
    session.flush()
    jobs.enqueue(session, jobs.PROCESS_DOCUMENTS, {"tender_id": tender.id}, dedupe_key=f"docs:{tender.id}")
    return doc


def process_documents(session: Session, tender: Tender, settings: Settings, connector: PortalConnector | None = None) -> dict:
    stats = {"downloaded": 0, "extracted": 0, "failed": 0, "blocked": 0}
    for doc in [d for d in tender.documents if d.status == "PENDING_DOWNLOAD"]:
        if connector is None:
            portal = session.get(Portal, tender.portal_id)
            connector = build_connector(portal.connector, portal.code, portal.config, settings)
        tmp = Path(settings.DOCUMENT_STORAGE_DIR) / str(tender.id)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            path, ctype, size = connector.download_document(DocumentRef(doc.source_url or "", doc.filename), tmp)
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            final = _storage_path(settings, tender.id, sha, doc.filename)
            path.replace(final)
            doc.storage_path, doc.sha256, doc.size_bytes, doc.content_type, doc.status = str(final), sha, size, ctype, "PENDING"
            stats["downloaded"] += 1
        except (HumanInterventionRequired, PortalAccessDenied) as e:
            doc.status, doc.error = "BLOCKED_HUMAN_REQUIRED", str(e)
            tender.documents_status, tender.blocker_note = "BLOCKED_HUMAN_REQUIRED", str(e)
            stats["blocked"] += 1
        except Exception as e:
            doc.status, doc.error = "FAILED", f"download: {e}"
            stats["failed"] += 1

    for doc in [d for d in tender.documents if d.status == "PENDING" and d.storage_path]:
        data = Path(doc.storage_path).read_bytes()
        res = process_bytes(data, doc.filename, settings)
        doc.status, doc.extraction_method, doc.page_count = res.status, res.method, res.page_count
        doc.content_type = doc.content_type or res.kind
        doc.extracted_text, doc.sections, doc.error = res.text or None, res.sections, res.error
        if res.warnings:
            doc.error = "; ".join(filter(None, [doc.error, *res.warnings]))
        for child in res.children:
            for c in flatten(child):
                if c.status == "CONTAINER":
                    continue
                session.add(TenderDocument(
                    tender_id=tender.id, filename=f"{doc.filename} › {c.filename}"[:500], origin="ZIP_MEMBER",
                    size_bytes=c.size, sha256=c.sha256, status=c.status, extraction_method=c.method,
                    page_count=c.page_count, extracted_text=c.text or None, sections=c.sections, error=c.error,
                    uploaded_by=doc.uploaded_by))
                stats["extracted" if c.status == "EXTRACTED" else "failed"] += 1
        stats["extracted" if res.status in ("EXTRACTED", "CONTAINER") else "failed"] += 1
    session.flush()
    session.refresh(tender)
    if any(d.status == "EXTRACTED" for d in tender.documents):
        tender.documents_status = "PROCESSED"
    audit.record(session, "DOCUMENTS_PROCESSED", tender_id=tender.id, details=stats)
    jobs.enqueue(session, jobs.ANALYZE_TENDER, {"tender_id": tender.id, "reason": "documents"}, dedupe_key=f"analyze:{tender.id}")
    session.commit()
    return stats


# ------------------------------------------------------------------ analysis
def build_context(tender: Tender, settings: Settings) -> TenderContext:
    docs = [DocText(d.filename, d.extracted_text or "", d.sections or {})
            for d in tender.documents if d.status == "EXTRACTED" and d.extracted_text]
    portal_text = (tender.raw or {}).get("portal_text") or ""
    full = "\n\n".join(([f"=== PORTAL TENDER DETAILS ===\n{portal_text}"] if portal_text else [])
                       + [f"=== {d.filename} ===\n{d.text}" for d in docs])
    llm_ctx, truncated = build_llm_context(docs, settings.LLM_MAX_CONTEXT_CHARS) if docs else ("", False)
    if portal_text:  # small and always relevant: sent ahead of document excerpts
        llm_ctx = f"=== PORTAL TENDER DETAILS (published on the portal page) ===\n{portal_text}" + (f"\n\n{llm_ctx}" if llm_ctx else "")
    return TenderContext(
        title=tender.title, organization=tender.organization, reference_number=tender.reference_number,
        closing_at=as_utc(tender.closing_at), published_at=as_utc(tender.published_at),
        portal_value_inr=tender.tender_value_inr, portal_emd_inr=tender.emd_inr, location=tender.location,
        category=tender.category, document_text=full, llm_context=llm_ctx, llm_context_truncated=truncated,
        has_documents=bool(docs), has_portal_detail=bool(portal_text),
    )


def _persist_decision(session: Session, t: Tender, d: Decision, ctx: TenderContext, catalog: Catalog) -> None:
    v = t.version
    for model in (TenderMatch, ExtractedRequirement, RejectionReason):
        session.execute(delete(model).where(model.tender_id == t.id, model.tender_version == v))

    for m in d.matches:
        session.add(TenderMatch(
            tender_id=t.id, tender_version=v, capability_id=m.capability_id, capability_name=m.capability_name,
            sub_capability=m.sub_capability, product_ids=m.product_ids, explicit_oem=m.explicit_oem,
            match_type=m.match_type, confidence=m.confidence, evidence=m.evidence, explanation=m.explanation,
            source=m.source))
    a = d.analysis
    if a is not None:
        for name, fv in a.fields.items():
            session.add(ExtractedRequirement(tender_id=t.id, tender_version=v, kind="FIELD", name=name, value=fv.value,
                                             confidence=fv.confidence, evidence=fv.evidence, source=fv.source))
        for r in a.requirements:
            session.add(ExtractedRequirement(
                tender_id=t.id, tender_version=v, kind="REQUIREMENT", name=(r.sub_capability or "requirement")[:100],
                value=r.text, component_kind=r.kind, confidence=r.confidence, evidence=r.evidence,
                source="LLM" if a.mode == "LLM" else "DETERMINISTIC"))
        for c in a.components:
            session.add(ExtractedRequirement(
                tender_id=t.id, tender_version=v, kind="COMPONENT", name=c.kind, value=c.description, component_kind=c.kind,
                value_inr=c.value_inr, evidence=c.evidence, source="LLM" if a.mode == "LLM" else "DETERMINISTIC"))
        t.extracted = {k: {"value": f.value, "confidence": f.confidence, "evidence": f.evidence, "source": f.source}
                       for k, f in a.fields.items()}
        t.extracted["_requirements"] = [
            {"text": r.text, "kind": r.kind, "capability_ids": r.capability_ids, "match_type": r.match_type,
             "confidence": r.confidence, "evidence": r.evidence, "explanation": r.explanation,
             "other_oems_named": r.other_oems_named} for r in a.requirements]
        t.extracted["_eligibility"] = {"assessment": a.eligibility_assessment, "issues": a.eligibility_issues}
        t.extracted["_risks"] = a.risks
        t.extracted["_notes"] = a.notes
        t.summary, t.recommendation = a.summary or None, a.recommendation or None
        t.analysis_mode, t.model_version = a.mode, a.model_version
    else:
        t.analysis_mode, t.model_version = "PREFILTER", "rules-v1"

    if d.score is not None:
        session.add(ScoreRow(tender_id=t.id, tender_version=v, total=d.score.total, priority=d.score.priority,
                             breakdown=d.score.breakdown, weights=d.score.weights))
    for stage, code, detail in d.rejections:
        session.add(RejectionReason(tender_id=t.id, tender_version=v, stage=stage, code=code, detail=detail))

    # manual reviews: supersede reviews from older versions, open one per new reason
    for old in session.scalars(select(ManualReview).where(ManualReview.tender_id == t.id, ManualReview.status == "OPEN")):
        if old.tender_version != v:
            old.status = "SUPERSEDED"
    if d.status == MANUAL_REVIEW:
        existing = {r.reason_code: r for r in session.scalars(select(ManualReview).where(
            ManualReview.tender_id == t.id, ManualReview.tender_version == v))}
        for code, reason in d.reviews:
            if code not in existing:
                session.add(ManualReview(tender_id=t.id, tender_version=v, reason_code=code, reason=reason))
            elif existing[code].status == "OPEN":
                existing[code].reason = reason  # re-analysis may have sharper wording/evidence

    top = d.matches[0] if d.matches else None
    t.decision, t.opportunity_type = d.status, d.opportunity_type
    t.priority = d.priority
    t.score = d.score.total if d.score else None
    t.match_confidence = top.confidence if top else None
    t.primary_capability = top.capability_name if top else None
    t.matched_capabilities = [m.capability_id for m in d.matches]
    prods: list[str] = []
    for m in d.matches:
        for p in (m.explicit_product_ids or m.product_ids):
            if p not in prods:
                prods.append(p)
    t.matched_products = prods
    t.service_value_inr = d.value_analysis.get("service_value_inr")
    t.product_value_inr = d.value_analysis.get("product_value_inr")
    t.value_analysis = d.value_analysis
    t.flags = d.flags
    t.relevance_reason = d.relevance_reason or None
    t.rejection_reason = d.rejections[0][2] if d.rejections else None
    t.pipeline_status, t.last_analyzed_at = "ANALYZED", utcnow()

    audit.record(session, "DECISION", tender_id=t.id, decision=d.status, reason=d.primary_reason,
                 model_version=t.model_version, details={
                     "tender_version": v, "source": t.source_url, "portal_tender_id": t.portal_tender_id,
                     "classification": d.opportunity_type, "detected_value": d.value_analysis,
                     "matched_capabilities": [{"id": m.capability_id, "type": m.match_type, "confidence": m.confidence,
                                               "source": m.source} for m in d.matches],
                     "matched_oems": [catalog.products[p].oem for p in prods if p in catalog.products][:10],
                     "explicit_products": sorted({p for m in d.matches for p in m.explicit_product_ids}),
                     "score": d.score.total if d.score else None, "priority": d.priority,
                     "score_breakdown": d.score.breakdown if d.score else None,
                     "rejections": d.rejections, "reviews": d.reviews, "flags": d.flags,
                     "analysis_mode": t.analysis_mode, "prefilter": d.prefilter.code or "PASSED",
                     "llm_context_truncated": ctx.llm_context_truncated,
                     "had_documents": ctx.has_documents,
                 })


def should_alert_immediately(t: Tender, settings: Settings) -> bool:
    if t.decision == ACCEPTED or (t.decision == MANUAL_REVIEW and settings.ALERT_ON_MANUAL_REVIEW):
        if t.priority == "HOT":
            return True
        if t.priority == "HIGH" and settings.HIGH_ALERT_MODE == "immediate":
            return True
    return False


def analyze_tender(session: Session, tender: Tender, settings: Settings, catalog: Catalog, analyzer: Analyzer,
                   now: datetime | None = None) -> Decision:
    tender.pipeline_status = "ANALYZING"
    ctx = build_context(tender, settings)
    decision = evaluate(ctx, catalog, settings, analyzer, now=now)
    _persist_decision(session, tender, decision, ctx, catalog)
    session.flush()
    if should_alert_immediately(tender, settings):
        jobs.enqueue(session, jobs.SEND_ALERT, {"tender_id": tender.id, "version": tender.version},
                     dedupe_key=f"alert:{tender.id}:v{tender.version}")
    session.commit()
    return decision

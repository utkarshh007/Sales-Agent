from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, HttpUrl
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit, jobs
from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, current_user, require_role
from app.catalog import get_catalog
from app.config import get_settings
from app.db import as_utc, get_db, utcnow
from app.documents.values import format_inr
from app.models import (
    Alert, AuditLog, ManualReview, Portal, RejectionReason, ScoreRow, Tender, TenderDocument, TenderMatch, TenderSource,
    TenderVersion, User,
)
from app.pipeline import store_upload
from app.security import create_access_token, hash_password, limiter, validate_password, verify_password

router = APIRouter(prefix="/api")


def _iso(dt: datetime | None) -> str | None:
    dt = as_utc(dt)
    return dt.isoformat() if dt else None


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = request.client.host if request.client else "?"
    if not limiter.allow(f"login:{ip}", s.LOGIN_RATE_LIMIT_PER_MINUTE) or \
            not limiter.allow(f"login:{body.email.lower()}", s.LOGIN_RATE_LIMIT_PER_MINUTE):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts; try again in a minute")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit.record(db, "LOGIN_FAILED", actor=body.email.lower(), details={"ip": ip})
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    token, csrf = create_access_token(user.id, user.role)
    secure = s.is_production
    max_age = s.ACCESS_TOKEN_MINUTES * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=secure, samesite="lax", path="/")
    user.last_login_at = utcnow()
    audit.record(db, "LOGIN", actor=user.email, details={"ip": ip})
    db.commit()
    return {"email": user.email, "role": user.role}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"email": user.email, "role": user.role}


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(current_user)):
    now = utcnow()
    live = Tender.decision.in_(("ACCEPTED", "MANUAL_REVIEW"))
    open_ = or_(Tender.closing_at.is_(None), Tender.closing_at >= now)

    def count(*conds) -> int:
        return db.scalar(select(func.count(Tender.id)).where(*conds)) or 0

    by_portal = []
    for p in db.scalars(select(Portal).where(Portal.enabled.is_(True)).order_by(Portal.id)):
        by_portal.append({"code": p.code, "name": p.name, "enabled": p.enabled, "last_run_at": _iso(p.last_run_at),
                          "last_status": p.last_status, "last_error": p.last_error, "blocker": p.blocker,
                          "tenders": db.scalar(select(func.count(TenderSource.id)).where(TenderSource.portal_id == p.id)) or 0})
    return {
        "new_24h": count(Tender.first_seen_at >= now - timedelta(hours=24)),
        "hot": count(live, open_, Tender.priority == "HOT"),
        "high": count(live, open_, Tender.priority == "HIGH"),
        "medium": count(live, open_, Tender.priority == "MEDIUM"),
        "closing_soon": count(live, Tender.closing_at >= now, Tender.closing_at <= now + timedelta(days=7)),
        "service": count(live, open_, Tender.opportunity_type == "SERVICE"),
        "oem": count(live, open_, Tender.opportunity_type == "OEM"),
        "hybrid": count(live, open_, Tender.opportunity_type == "HYBRID"),
        "accepted": count(Tender.decision == "ACCEPTED", open_),
        "manual_review": db.scalar(select(func.count(ManualReview.id)).where(ManualReview.status == "OPEN")) or 0,
        "rejected": count(Tender.decision == "REJECTED"),
        "pending": count(Tender.decision == "PENDING"),
        "awaiting_documents": count(live, open_, Tender.documents_status == "BLOCKED_HUMAN_REQUIRED"),
        "total": count(),
        "portals": by_portal,
        "analysis_mode": "LLM" if get_settings().llm_available else "RULES_ONLY",
    }


# ------------------------------------------------------------------ tenders
def _product_name(pid: str) -> str:
    p = get_catalog().products.get(pid)
    return p.name if p else pid


def _row(t: Tender) -> dict:
    return {
        "id": t.id, "score": t.score, "priority": t.priority, "title": t.title, "organization": t.organization,
        "location": t.location, "opportunity_type": t.opportunity_type, "matched_capability": t.primary_capability,
        "matched_products": [_product_name(p) for p in t.matched_products[:4]], "tender_value_inr": (t.value_analysis or {}).get("total_value_inr"),
        "service_value_inr": t.service_value_inr, "closing_at": _iso(t.closing_at), "published_at": _iso(t.published_at),
        "match_confidence": t.match_confidence, "decision": t.decision, "pipeline_status": t.pipeline_status,
        "documents_status": t.documents_status, "flags": t.flags, "portal_code": t.portal.code if t.portal else None,
        "reference_number": t.reference_number, "portal_tender_id": t.portal_tender_id,
        "source_count": len(t.sources) or 1,
    }


def _rows_with_scores(db: Session, tenders: list[Tender]) -> list[dict]:
    """List rows plus each tender's latest per-component score (drives the segmented score bar)."""
    latest: dict[int, ScoreRow] = {}
    if tenders:
        for sr in db.scalars(select(ScoreRow).where(ScoreRow.tender_id.in_([t.id for t in tenders])).order_by(ScoreRow.id)):
            latest[sr.tender_id] = sr  # last one wins
    out = []
    for t in tenders:
        item = _row(t)
        sr = latest.get(t.id)
        item["score_parts"] = {k: [v["points"], v["max"]] for k, v in sr.breakdown.items()} if sr else None
        out.append(item)
    return out


SortKey = Literal["score", "closing_at", "published_at", "first_seen_at"]


@router.get("/tenders")
def list_tenders(
    db: Session = Depends(get_db), _: User = Depends(current_user),
    decision: str | None = Query(None, pattern="^(ACCEPTED|REJECTED|MANUAL_REVIEW|PENDING|LIVE)$"),
    priority: str | None = Query(None, pattern="^(HOT|HIGH|MEDIUM|LOW|SUPPRESS)$"),
    opportunity_type: str | None = Query(None, pattern="^(SERVICE|OEM|HYBRID|UNRELATED|UNKNOWN)$"),
    portal: str | None = Query(None, max_length=50),
    q: str | None = Query(None, max_length=200),
    closing_within_days: int | None = Query(None, ge=0, le=365),
    include_closed: bool = False,
    sort: SortKey = "score",
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
):
    now = utcnow()
    conds = []
    if decision == "LIVE":
        conds.append(Tender.decision.in_(("ACCEPTED", "MANUAL_REVIEW")))
    elif decision:
        conds.append(Tender.decision == decision)
    if priority:
        conds.append(Tender.priority == priority)
    if opportunity_type:
        conds.append(Tender.opportunity_type == opportunity_type)
    if portal:  # a tender matches if any portal it is listed on matches
        conds.append(or_(Tender.portal.has(Portal.code == portal),
                         Tender.sources.any(TenderSource.portal.has(Portal.code == portal))))
    if q:
        like = f"%{q}%"
        conds.append(or_(Tender.title.ilike(like), Tender.organization.ilike(like), Tender.reference_number.ilike(like),
                         Tender.portal_tender_id.ilike(like)))
    if closing_within_days is not None:
        conds += [Tender.closing_at >= now, Tender.closing_at <= now + timedelta(days=closing_within_days)]
    elif not include_closed:
        conds.append(or_(Tender.closing_at.is_(None), Tender.closing_at >= now))
    order = {
        "score": (Tender.score.desc().nulls_last(), Tender.closing_at.asc()),
        "closing_at": (Tender.closing_at.asc().nulls_last(),),
        "published_at": (Tender.published_at.desc().nulls_last(),),
        "first_seen_at": (Tender.first_seen_at.desc(),),
    }[sort]
    total = db.scalar(select(func.count(Tender.id)).where(*conds)) or 0
    rows = db.scalars(select(Tender).where(*conds).order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size, "items": _rows_with_scores(db, rows)}


@router.get("/tenders/{tender_id}")
def tender_detail(tender_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(404, "Tender not found")
    cat = get_catalog()
    v = t.version
    matches = db.scalars(select(TenderMatch).where(TenderMatch.tender_id == t.id, TenderMatch.tender_version == v)
                         .order_by(TenderMatch.confidence.desc())).all()
    score = db.scalar(select(ScoreRow).where(ScoreRow.tender_id == t.id).order_by(ScoreRow.id.desc()).limit(1))
    return {
        **_row(t),
        "version": v, "source_url": t.source_url, "department": t.department, "category": t.category,
        "tender_type": t.tender_type, "opening_at": _iso(t.opening_at), "emd_inr": t.emd_inr,
        "corrigendum": t.corrigendum, "contact_info": t.contact_info, "blocker_note": t.blocker_note,
        "first_seen_at": _iso(t.first_seen_at), "last_analyzed_at": _iso(t.last_analyzed_at),
        "analysis_mode": t.analysis_mode, "model_version": t.model_version,
        "relevance_reason": t.relevance_reason, "rejection_reason": t.rejection_reason, "summary": t.summary,
        "recommendation": t.recommendation, "extracted": t.extracted, "value_analysis": t.value_analysis,
        "portal_text": (t.raw or {}).get("portal_text"), "document_names": (t.raw or {}).get("document_names"),
        "sources": [{"portal_code": src.portal.code, "portal_name": src.portal.name, "match_basis": src.match_basis,
                     "source_url": src.source_url, "lookup_hint": src.lookup_hint,
                     "first_seen_at": _iso(src.first_seen_at), "last_seen_at": _iso(src.last_seen_at)}
                    for src in t.sources],
        "value_display": {k: format_inr(t.value_analysis.get(k)) for k in ("total_value_inr", "service_value_inr", "product_value_inr", "emd_inr")}
        if t.value_analysis else {},
        "matches": [{
            "capability_id": m.capability_id, "capability_name": m.capability_name, "sub_capability": m.sub_capability,
            "match_type": m.match_type, "confidence": m.confidence, "evidence": m.evidence, "explanation": m.explanation,
            "source": m.source, "explicit_oem": m.explicit_oem,
            "products": [{"id": p, "name": cat.products[p].name, "oem": cat.products[p].oem}
                         for p in m.product_ids if p in cat.products],
        } for m in matches],
        "score_breakdown": score.breakdown if score else None,
        "documents": [{
            "id": d.id, "filename": d.filename, "origin": d.origin, "status": d.status, "size_bytes": d.size_bytes,
            "method": d.extraction_method, "pages": d.page_count, "sections": sorted((d.sections or {}).keys()),
            "error": d.error, "uploaded_by": d.uploaded_by, "created_at": _iso(d.created_at), "source_url": d.source_url,
            "chars": len(d.extracted_text or ""),
        } for d in sorted(t.documents, key=lambda d: d.id)],
        "versions": [{"version": x.version, "change": x.change_summary, "created_at": _iso(x.created_at)}
                     for x in db.scalars(select(TenderVersion).where(TenderVersion.tender_id == t.id).order_by(TenderVersion.version))],
        "rejections": [{"stage": r.stage, "code": r.code, "detail": r.detail, "version": r.tender_version}
                       for r in db.scalars(select(RejectionReason).where(RejectionReason.tender_id == t.id, RejectionReason.tender_version == v))],
        "reviews": [{"id": r.id, "code": r.reason_code, "reason": r.reason, "status": r.status, "resolution": r.resolution,
                     "notes": r.notes, "resolved_by": r.resolved_by, "version": r.tender_version}
                    for r in db.scalars(select(ManualReview).where(ManualReview.tender_id == t.id).order_by(ManualReview.id.desc()))],
        "alerts": [{"kind": a.kind, "status": a.status, "subject": a.subject, "created_at": _iso(a.created_at)}
                   for a in db.scalars(select(Alert).where(Alert.tender_id == t.id).order_by(Alert.id.desc()))],
        "audit": [{"action": a.action, "actor": a.actor, "decision": a.decision, "reason": a.reason,
                   "model_version": a.model_version, "created_at": _iso(a.created_at), "details": a.details}
                  for a in db.scalars(select(AuditLog).where(AuditLog.tender_id == t.id).order_by(AuditLog.id.desc()).limit(50))],
    }


@router.post("/tenders/{tender_id}/documents", status_code=201)
async def upload_document(tender_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(require_role("analyst"))):
    s = get_settings()
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(404, "Tender not found")
    data = await file.read(s.MAX_DOCUMENT_BYTES + 1)
    if len(data) > s.MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"File exceeds {s.MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, "Empty file")
    doc = store_upload(db, t, file.filename or "document", data, s, user.email)
    db.commit()
    return {"id": doc.id, "status": doc.status}


@router.post("/tenders/{tender_id}/reanalyze", status_code=202)
def reanalyze(tender_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("analyst"))):
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(404, "Tender not found")
    jobs.enqueue(db, jobs.ANALYZE_TENDER, {"tender_id": t.id, "reason": "manual"}, dedupe_key=f"analyze:{t.id}")
    t.pipeline_status = "QUEUED"
    audit.record(db, "REANALYZE_REQUESTED", tender_id=t.id, actor=user.email)
    db.commit()
    return {"queued": True}


class ManualTenderIn(BaseModel):
    title: str = Field(min_length=5, max_length=2000)
    organization: str | None = Field(None, max_length=500)
    reference_number: str | None = Field(None, max_length=300)
    closing_at: datetime | None = None
    tender_value_inr: int | None = Field(None, ge=0, le=10**13)
    source_url: HttpUrl | None = None
    location: str | None = Field(None, max_length=300)


@router.post("/tenders", status_code=201)
def create_manual_tender(body: ManualTenderIn, db: Session = Depends(get_db), user: User = Depends(require_role("analyst"))):
    import hashlib

    portal = db.scalar(select(Portal).where(Portal.code == "manual"))
    if portal is None:
        raise HTTPException(500, "manual portal missing; run `python -m app.cli seed`")
    key = f"manual|{body.reference_number or ''}|{body.title}|{body.organization or ''}"
    fp = hashlib.sha256(key.encode()).hexdigest()
    if db.scalar(select(Tender.id).where(Tender.fingerprint == fp)):
        raise HTTPException(409, "This tender already exists")
    t = Tender(portal_id=portal.id, fingerprint=fp, content_hash=fp, version=1, title=body.title,
               organization=body.organization, reference_number=body.reference_number,
               closing_at=as_utc(body.closing_at), tender_value_inr=body.tender_value_inr,
               source_url=str(body.source_url) if body.source_url else None, location=body.location,
               pipeline_status="QUEUED", raw={"entered_by": user.email})
    t.sources.append(TenderSource(portal_id=portal.id, fingerprint=fp, portal_tender_id=None, source_url=t.source_url,
                                  listing_hash=fp, match_basis="PRIMARY"))
    db.add(t)
    db.flush()
    db.add(TenderVersion(tender_id=t.id, version=1, content_hash=fp, snapshot=body.model_dump(mode="json"), change_summary="manual entry"))
    audit.record(db, "TENDER_NEW", tender_id=t.id, actor=user.email, details={"manual": True})
    jobs.enqueue(db, jobs.ANALYZE_TENDER, {"tender_id": t.id}, dedupe_key=f"analyze:{t.id}")
    db.commit()
    return {"id": t.id}


# ------------------------------------------------------------------ manual review
@router.get("/reviews")
def list_reviews(status_: str = Query("OPEN", alias="status", pattern="^(OPEN|RESOLVED|SUPERSEDED)$"),
                 db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.execute(select(ManualReview, Tender).join(Tender, Tender.id == ManualReview.tender_id)
                      .where(ManualReview.status == status_).order_by(Tender.score.desc().nulls_last()).limit(500)).all()
    scored = {row["id"]: row for row in _rows_with_scores(db, list({t.id: t for _, t in rows}.values()))}
    return [{"id": r.id, "code": r.reason_code, "reason": r.reason, "status": r.status, "created_at": _iso(r.created_at),
             "resolution": r.resolution, "resolved_by": r.resolved_by, "tender": scored[t.id]} for r, t in rows]


class ResolveIn(BaseModel):
    resolution: Literal["ACCEPTED", "REJECTED"]
    notes: str = Field("", max_length=4000)


@router.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: int, body: ResolveIn, db: Session = Depends(get_db), user: User = Depends(require_role("analyst"))):
    r = db.get(ManualReview, review_id)
    if r is None or r.status != "OPEN":
        raise HTTPException(404, "Open review not found")
    r.status, r.resolution, r.notes, r.resolved_by, r.resolved_at = "RESOLVED", body.resolution, body.notes, user.email, utcnow()
    t = db.get(Tender, r.tender_id)
    others_open = db.scalar(select(func.count(ManualReview.id)).where(
        ManualReview.tender_id == t.id, ManualReview.status == "OPEN", ManualReview.id != r.id)) or 0
    if body.resolution == "REJECTED":
        t.decision, t.rejection_reason = "REJECTED", f"Rejected in manual review by {user.email}: {body.notes or r.reason}"
        for o in db.scalars(select(ManualReview).where(ManualReview.tender_id == t.id, ManualReview.status == "OPEN")):
            o.status, o.resolution, o.resolved_by, o.resolved_at = "RESOLVED", "REJECTED", user.email, utcnow()
    elif others_open == 0:
        t.decision = "ACCEPTED"
        from app.pipeline import should_alert_immediately
        if should_alert_immediately(t, get_settings()):
            jobs.enqueue(db, jobs.SEND_ALERT, {"tender_id": t.id, "version": t.version}, dedupe_key=f"alert:{t.id}:v{t.version}")
    audit.record(db, "MANUAL_REVIEW_RESOLVED", tender_id=t.id, actor=user.email, decision=t.decision,
                 reason=body.notes or r.reason, details={"review_id": r.id, "code": r.reason_code, "resolution": body.resolution})
    db.commit()
    return {"tender_decision": t.decision}


# ------------------------------------------------------------------ portals / config / catalog
@router.get("/portals")
def list_portals(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{"id": p.id, "code": p.code, "name": p.name, "connector": p.connector, "acquisition_method": p.acquisition_method,
             "enabled": p.enabled, "schedule_minutes": p.schedule_minutes, "config": p.config,
             "last_run_at": _iso(p.last_run_at), "last_status": p.last_status, "last_error": p.last_error, "blocker": p.blocker}
            for p in db.scalars(select(Portal).order_by(Portal.id))]


class PortalPatch(BaseModel):
    enabled: bool | None = None
    schedule_minutes: int | None = Field(None, ge=0, le=24 * 60)


@router.patch("/portals/{portal_id}")
def update_portal(portal_id: int, body: PortalPatch, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "Portal not found")
    changes = body.model_dump(exclude_none=True)
    for k, val in changes.items():
        setattr(p, k, val)
    audit.record(db, "PORTAL_UPDATED", actor=user.email, details={"portal": p.code, **changes})
    db.commit()
    return {"ok": True}


@router.post("/portals/{portal_id}/run", status_code=202)
def run_portal(portal_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    p = db.get(Portal, portal_id)
    if p is None or p.connector == "manual":
        raise HTTPException(404, "Portal not found or has no connector")
    job = jobs.enqueue(db, jobs.DISCOVER_PORTAL, {"portal_id": p.id}, dedupe_key=f"discover:{p.code}")
    audit.record(db, "DISCOVERY_REQUESTED", actor=user.email, details={"portal": p.code})
    db.commit()
    return {"queued": job is not None}


class BuyerPageIn(BaseModel):
    """An organisation's own tender page. Advanced options mirror app/connectors/buyer_page.py."""
    name: str = Field(min_length=3, max_length=200)
    url: HttpUrl
    organization: str = Field(min_length=2, max_length=300)
    render: Literal["http", "browser"] = "http"
    follow_detail: bool = False
    screen: Literal["title", "documents"] = "title"
    reference_regex: str | None = Field(None, max_length=200)
    schedule_minutes: int = Field(360, ge=30, le=24 * 60)


def _buyer_config(body: BuyerPageIn) -> dict:
    import re as _re
    if body.reference_regex:
        try:
            if "ref" not in _re.compile(body.reference_regex).groupindex:
                raise HTTPException(400, "reference_regex needs a named group (?P<ref>…)")
        except _re.error as e:
            raise HTTPException(400, f"reference_regex is not a valid regular expression: {e}") from e
    cfg = {"pages": [{"url": str(body.url), "organization": body.organization}], "render": body.render,
           "follow_detail": body.follow_detail, "screen": body.screen}
    if body.reference_regex:
        cfg["reference_regex"] = body.reference_regex
    return cfg


def _check_permitted(url: str) -> None:
    from app.connectors.base import PoliteHttpClient, PortalAccessDenied
    http = PoliteHttpClient(get_settings(), delay_seconds=0)
    try:
        if not http._allowed(url):
            raise HTTPException(400, "This site's robots.txt does not allow automated reading of that page.")
    except PortalAccessDenied as e:
        raise HTTPException(400, str(e)) from e
    finally:
        http.close()


@router.post("/portals/preview")
def preview_buyer_page(body: BuyerPageIn, user: User = Depends(require_role("admin"))):
    """Dry run: read the page once and show what would be extracted. Nothing is saved."""
    from app.connectors.buyer_page import BuyerPageConnector

    _check_permitted(str(body.url))
    connector = BuyerPageConnector("preview", _buyer_config(body), get_settings())
    try:
        result = connector.discover(lambda fp: False, None)
    finally:
        connector.http.close()
    return {
        "robots_allowed": True,
        "open_tenders": len(result.items),
        "blockers": result.blockers, "errors": result.errors,
        "items": [{"title": i.title, "reference": i.reference_number, "organization": i.organization,
                   "published_at": _iso(i.published_at), "closing_at": _iso(i.closing_at),
                   "documents": len(i.raw.get("doc_links", [])), "detail_url": i.raw.get("detail_url")}
                  for i in result.items[:15]],
    }


class BuyerPageCreate(BuyerPageIn):
    permission_confirmed: bool
    permission_note: str = Field(min_length=10, max_length=1000,
                                 description="Why automated access is permitted (terms reviewed, robots.txt, agreement…)")


@router.post("/portals", status_code=201)
def create_buyer_page(body: BuyerPageCreate, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    import re as _re
    if not body.permission_confirmed:
        raise HTTPException(400, "Confirm that the site permits automated reading of its tender page.")
    _check_permitted(str(body.url))
    code = "buyer_" + _re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_")[:40]
    if db.scalar(select(Portal.id).where(Portal.code == code)):
        raise HTTPException(409, "A source with this name already exists")
    p = Portal(code=code, name=body.name, connector="buyer_page", base_url=str(body.url),
               acquisition_method="BROWSER" if body.render == "browser" else "HTML", enabled=True,
               schedule_minutes=body.schedule_minutes, config=_buyer_config(body), blocker=None)
    db.add(p)
    db.flush()
    audit.record(db, "PORTAL_CREATED", actor=user.email, reason=body.permission_note,
                 details={"portal": code, "url": str(body.url), "render": body.render,
                          "permission_confirmed_by": user.email, "permission_note": body.permission_note})
    db.commit()
    return {"id": p.id, "code": code}


@router.get("/settings")
def get_public_settings(_: User = Depends(current_user)):
    return get_settings().public_view()


@router.get("/catalog")
def catalog(_: User = Depends(current_user)):
    c = get_catalog()
    return {
        "categories": c.categories,
        "capabilities": [{"id": x.id, "name": x.name, "category": x.category_code, "offering": x.offering,
                          "sub_capabilities": sorted({r.sub for r in x.rules}),
                          "products": [p.id for p in c.products_for(x.id)]} for x in c.capabilities.values()],
        "products": [{"id": p.id, "name": p.name, "oem": p.oem, "capabilities": p.capabilities} for p in c.products.values()],
    }


# ------------------------------------------------------------------ users (admin)
class UserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "analyst", "viewer"] = "viewer"


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return [{"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "last_login_at": _iso(u.last_login_at)}
            for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    problem = validate_password(body.password)
    if problem:
        raise HTTPException(400, problem)
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "User exists")
    db.add(User(email=body.email.lower(), password_hash=hash_password(body.password), role=body.role))
    audit.record(db, "USER_CREATED", actor=admin.email, details={"email": body.email.lower(), "role": body.role})
    db.commit()
    return {"ok": True}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}

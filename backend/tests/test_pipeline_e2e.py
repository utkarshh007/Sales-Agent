"""Phase 1 end-to-end: discovery -> dedupe -> rules -> analysis -> matching -> scoring -> persistence
-> alerts -> update detection -> document upload -> re-analysis."""
from __future__ import annotations

import io

import docx
import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Alert, AuditLog, ManualReview, Portal, ProcessingJob, ScoreRow, Tender, TenderVersion
from app.pipeline import discover_portal, store_upload
from app.worker import Worker
from tests.conftest import StubAnalyzer, llm_analysis
from tests.helpers import CYBER_ROWS, fixture_connector, make_db, portal_date


@pytest.fixture
def env(tmp_path, settings):
    settings.DOCUMENT_STORAGE_DIR = str(tmp_path / "docs")
    return make_db(tmp_path), settings


def _run(Session, settings, connector, analyzer=None):
    with Session() as s:
        portal = s.scalar(select(Portal).where(Portal.code == "cppp"))
        summary = discover_portal(s, portal, settings, connector)
    w = Worker(settings, analyzer=analyzer)
    w.drain()
    return summary


def test_end_to_end_discovery_to_alert(env):
    Session, settings = env
    connector, transport = fixture_connector(settings)
    summary = _run(Session, settings, connector)
    assert summary["NEW"] == 14  # 4 synthetic + 10 real CPPP rows
    assert summary["pages"] == 2

    with Session() as s:
        by_id = {t.portal_tender_id: t for t in s.scalars(select(Tender))}
        siem, redteam = by_id["2026_NIC_1001"], by_id["2026_BANK_2002"]
        civil, guards = by_id["2026_CPWD_3003"], by_id["2026_PSU_4004"]

        assert siem.decision == "ACCEPTED" and siem.opportunity_type == "OEM"
        assert "splunk" in siem.matched_products and siem.priority in ("HOT", "HIGH")
        assert siem.documents_status == "BLOCKED_HUMAN_REQUIRED" and "CAPTCHA" in siem.blocker_note

        # Service tender with no stated value -> manual review (configurable)
        assert redteam.opportunity_type == "SERVICE" and redteam.decision == "MANUAL_REVIEW"
        assert s.scalar(select(ManualReview).where(ManualReview.tender_id == redteam.id)).reason_code == "SERVICE_VALUE_UNKNOWN"

        assert civil.decision == "REJECTED" and civil.opportunity_type == "UNRELATED"
        assert guards.decision == "REJECTED", "physical security guards are not cyber"
        # every real CPPP row on the captured page is civil/electrical (or closed by now) -> all rejected
        real = [t for t in by_id.values() if not t.portal_tender_id.startswith(("2026_NIC", "2026_BANK", "2026_CPWD", "2026_PSU"))]
        assert len(real) == 10 and all(t.decision == "REJECTED" for t in real)

        # audit trail for every decision
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "DECISION")) == 14
        audit = s.scalar(select(AuditLog).where(AuditLog.action == "DECISION", AuditLog.tender_id == siem.id))
        assert audit.details["classification"] == "OEM" and audit.details["score"] == siem.score
        assert s.scalar(select(ScoreRow).where(ScoreRow.tender_id == siem.id)).breakdown["oem"]["points"] == 20

        # immediate alert stored (SMTP not configured in tests)
        alert = s.scalar(select(Alert).where(Alert.tender_id == siem.id))
        assert alert.status == "NOT_CONFIGURED" and alert.subject.startswith(f"[{siem.priority}]")
        assert "Splunk" in alert.subject
        assert s.scalar(select(func.count(ProcessingJob.id)).where(ProcessingJob.status == "FAILED")) == 0


def test_rediscovery_is_idempotent_and_updates_create_versions(env):
    Session, settings = env
    connector, _ = fixture_connector(settings)
    _run(Session, settings, connector)
    with Session() as s:
        jobs_before = s.scalar(select(func.count(ProcessingJob.id)))

    connector, _ = fixture_connector(settings)
    summary = _run(Session, settings, connector)
    assert summary["NEW"] == 0 and summary["UPDATED"] == 0
    with Session() as s:
        assert s.scalar(select(func.count(ProcessingJob.id))) == jobs_before, "unchanged tenders must not be re-queued"

    rows = [dict(r) for r in CYBER_ROWS]
    rows[0]["close"] = portal_date(40)  # corrigendum extends the closing date
    connector, _ = fixture_connector(settings, rows)
    summary = _run(Session, settings, connector)
    assert summary["UPDATED"] == 1
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_NIC_1001"))
        assert t.version == 2
        v2 = s.scalar(select(TenderVersion).where(TenderVersion.tender_id == t.id, TenderVersion.version == 2))
        assert "closing_at" in v2.change_summary
        assert s.scalar(select(func.count(ScoreRow.id)).where(ScoreRow.tender_id == t.id)) == 2  # re-analysed


def test_llm_is_only_called_for_tenders_that_pass_deterministic_filters(env):
    Session, settings = env
    stub = StubAnalyzer(llm_analysis([], []))
    connector, _ = fixture_connector(settings)
    _run(Session, settings, connector, analyzer=stub)
    assert stub.calls == 2  # SIEM + red team; 12 non-cyber tenders never reach the LLM


def _docx_bytes(paragraphs: list[str]) -> bytes:
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_uploaded_documents_resolve_value_and_requalify(env):
    Session, settings = env
    connector, _ = fixture_connector(settings)
    _run(Session, settings, connector)
    data = _docx_bytes([
        "Notice Inviting Tender",
        "Scope of Work",
        "Red team assessment covering external, internal and social engineering vectors for 6 weeks.",
        "Estimated Cost of the work: Rs. 18,00,000 (Rupees Eighteen Lakh only)",
        "Eligibility Criteria",
        "Bidder must be CERT-In empanelled with ISO 27001 certification.",
    ])
    with Session() as s:
        t = s.scalar(select(Tender).where(Tender.portal_tender_id == "2026_BANK_2002"))
        store_upload(s, t, "NIT.docx", data, settings, "analyst@example.com")
        s.commit()
        tid = t.id
    Worker(settings).drain()
    with Session() as s:
        t = s.get(Tender, tid)
        assert t.documents_status == "PROCESSED"
        assert t.documents[0].status == "EXTRACTED" and "eligibility" in t.documents[0].sections
        assert t.value_analysis["total_value_inr"] == 1_800_000
        assert t.decision == "ACCEPTED", "₹18 lakh service tender qualifies once the value is known"
        assert s.scalar(select(ManualReview).where(ManualReview.tender_id == tid)).status in ("OPEN", "RESOLVED", "SUPERSEDED")
        assert all(r.status != "OPEN" or r.tender_version == t.version
                   for r in s.scalars(select(ManualReview).where(ManualReview.tender_id == tid)))


def test_failed_jobs_retry_then_fail(env):
    Session, settings = env
    with Session() as s:
        jobs.enqueue(s, "BOGUS", {})
        s.commit()
    w = Worker(settings)
    w.run_job()
    with Session() as s:
        j = s.scalar(select(ProcessingJob).where(ProcessingJob.job_type == "BOGUS"))
        assert j.status == "QUEUED" and j.attempts == 1 and "unknown job type" in j.last_error

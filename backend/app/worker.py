"""Background worker: portal scheduler + job runner (section 8 / 22).

The LLM is never polled or run continuously — it is invoked only by ANALYZE_TENDER jobs, which exist
only for tenders that are new or materially updated.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import jobs
from app.alerts.email import send_digest, send_tender_alert
from app.catalog import get_catalog
from app.config import Settings, get_settings
from app.connectors.registry import CONNECTORS
from app.db import SessionLocal, as_utc, utcnow
from app.llm.analyzer import Analyzer, LLMUnavailable, build_analyzer
from app.models import Alert, Portal, Tender
from app.pipeline import analyze_tender, discover_portal, process_documents

log = logging.getLogger("worker")
IST = ZoneInfo("Asia/Kolkata")


class Worker:
    def __init__(self, settings: Settings | None = None, analyzer: Analyzer | None = None):
        self.settings = settings or get_settings()
        self.catalog = get_catalog()
        self.analyzer = analyzer or build_analyzer(self.settings, self.catalog)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    # ------------------------------------------------------------ scheduling
    def schedule(self) -> None:
        now = utcnow()
        with SessionLocal() as s:
            for p in s.scalars(select(Portal).where(Portal.enabled.is_(True), Portal.schedule_minutes > 0)):
                if p.connector not in CONNECTORS:
                    continue
                last = as_utc(p.last_run_at)
                if last is None or now - last >= timedelta(minutes=p.schedule_minutes):
                    jobs.enqueue(s, jobs.DISCOVER_PORTAL, {"portal_id": p.id}, dedupe_key=f"discover:{p.code}")
            local = now.astimezone(IST)
            if local.hour >= self.settings.DIGEST_HOUR_IST:
                key = f"digest:{local:%Y-%m-%d}"
                if s.scalar(select(Alert.id).where(Alert.dedupe_key == key)) is None:
                    jobs.enqueue(s, jobs.SEND_DIGEST, {}, dedupe_key=key)
            s.commit()

    # ------------------------------------------------------------ execution
    def run_job(self) -> bool:
        with SessionLocal() as s:
            job = jobs.claim(s, self.worker_id)
            if job is None:
                return False
            log.info("job %s %s %s", job.id, job.job_type, job.payload)
            try:
                result = self._dispatch(s, job.job_type, job.payload)
                jobs.complete(s, job, result if isinstance(result, dict) else None)
            except LLMUnavailable as e:
                s.rollback()
                jobs.fail(s, job, f"LLM unavailable: {e}", self.settings.JOB_MAX_ATTEMPTS)
            except Exception as e:
                s.rollback()
                log.exception("job %s failed", job.id)
                jobs.fail(s, job, f"{type(e).__name__}: {e}", self.settings.JOB_MAX_ATTEMPTS)
                if job.job_type == jobs.ANALYZE_TENDER:
                    t = s.get(Tender, job.payload.get("tender_id"))
                    if t is not None and job.status == "FAILED":
                        t.pipeline_status = "ERROR"
                        s.commit()
            return True

    def _dispatch(self, s, job_type: str, payload: dict):
        st, cat = self.settings, self.catalog
        if job_type == jobs.DISCOVER_PORTAL:
            portal = s.get(Portal, payload["portal_id"])
            return discover_portal(s, portal, st) if portal and portal.enabled else {"skipped": True}
        if job_type == jobs.PROCESS_DOCUMENTS:
            t = s.get(Tender, payload["tender_id"])
            return process_documents(s, t, st) if t else {"skipped": True}
        if job_type == jobs.ANALYZE_TENDER:
            t = s.get(Tender, payload["tender_id"])
            if t is None:
                return {"skipped": True}
            d = analyze_tender(s, t, st, cat, self.analyzer)
            return {"decision": d.status, "score": d.score.total if d.score else None}
        if job_type == jobs.SEND_ALERT:
            a = send_tender_alert(s, payload["tender_id"], payload["version"], st, cat)
            return {"alert": a.status if a else None}
        if job_type == jobs.SEND_DIGEST:
            a = send_digest(s, st, cat)
            return {"digest": a.status if a else "nothing to send"}
        raise ValueError(f"unknown job type {job_type}")

    def drain(self, max_jobs: int = 100_000) -> int:
        n = 0
        while n < max_jobs and self.run_job():
            n += 1
        return n

    def run_forever(self) -> None:
        log.info("worker %s started (analysis mode: %s)", self.worker_id,
                 "LLM" if self.settings.llm_available else "RULES_ONLY")
        last_recover = 0.0
        while True:
            try:
                if time.monotonic() - last_recover > 600:
                    with SessionLocal() as s:
                        jobs.recover_stale(s)
                    last_recover = time.monotonic()
                self.schedule()
                if not self.run_job():
                    time.sleep(self.settings.WORKER_POLL_SECONDS)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("worker loop error")
                time.sleep(self.settings.WORKER_POLL_SECONDS)

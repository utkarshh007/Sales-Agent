"""Durable DB-backed job queue. On PostgreSQL, claims use SELECT ... FOR UPDATE SKIP LOCKED so any
number of workers can run concurrently; SQLite (dev/tests) runs a single worker."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import ProcessingJob

log = logging.getLogger(__name__)

DISCOVER_PORTAL = "DISCOVER_PORTAL"
PROCESS_DOCUMENTS = "PROCESS_DOCUMENTS"
ANALYZE_TENDER = "ANALYZE_TENDER"
SEND_ALERT = "SEND_ALERT"
SEND_DIGEST = "SEND_DIGEST"


def enqueue(session: Session, job_type: str, payload: dict[str, Any] | None = None, *,
            dedupe_key: str | None = None, run_after: datetime | None = None) -> ProcessingJob | None:
    """Queue a job unless an identical one (same dedupe_key) is already queued or running."""
    if dedupe_key:
        existing = session.scalar(select(ProcessingJob).where(
            ProcessingJob.dedupe_key == dedupe_key, ProcessingJob.status.in_(("QUEUED", "RUNNING"))))
        if existing:
            return None
    job = ProcessingJob(job_type=job_type, payload=payload or {}, dedupe_key=dedupe_key,
                        run_after=run_after or utcnow())
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker_id: str) -> ProcessingJob | None:
    q = (select(ProcessingJob)
         .where(ProcessingJob.status == "QUEUED", ProcessingJob.run_after <= utcnow())
         .order_by(ProcessingJob.run_after, ProcessingJob.id)
         .limit(1))
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    job = session.scalar(q)
    if job is None:
        return None
    job.status, job.locked_by, job.locked_at = "RUNNING", worker_id, utcnow()
    job.attempts += 1
    session.commit()
    return job


def complete(session: Session, job: ProcessingJob, result: dict[str, Any] | None = None) -> None:
    job.status, job.finished_at, job.result = "DONE", utcnow(), result
    session.commit()


def fail(session: Session, job: ProcessingJob, error: str, max_attempts: int, retryable: bool = True) -> None:
    job.last_error = error[:4000]
    if retryable and job.attempts < max_attempts:
        job.status = "QUEUED"
        job.run_after = utcnow() + timedelta(minutes=2 ** job.attempts)
    else:
        job.status, job.finished_at = "FAILED", utcnow()
    job.locked_by = None
    session.commit()


def recover_stale(session: Session, older_than_minutes: int = 30) -> int:
    """Requeue jobs whose worker died mid-run."""
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    stale = session.scalars(select(ProcessingJob).where(ProcessingJob.status == "RUNNING",
                                                        ProcessingJob.locked_at < cutoff)).all()
    for j in stale:
        j.status, j.locked_by = "QUEUED", None
    session.commit()
    return len(stale)

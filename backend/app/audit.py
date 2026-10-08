from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(session: Session, action: str, *, tender_id: int | None = None, actor: str = "system",
           decision: str | None = None, reason: str | None = None, details: dict[str, Any] | None = None,
           model_version: str | None = None) -> AuditLog:
    entry = AuditLog(tender_id=tender_id, actor=actor, action=action, decision=decision, reason=reason,
                     details=details or {}, model_version=model_version)
    session.add(entry)
    return entry

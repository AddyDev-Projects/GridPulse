import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, Incident, WorkflowTransition

log = logging.getLogger("gridpulse.audit")


def record(
    db: Session,
    event_type: str,
    message: str,
    *,
    incident_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
    actor: str = "system",
    severity: str = "info",
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    ev = AuditEvent(incident_id=incident_id, plan_id=plan_id, event_type=event_type, actor=actor,
                    severity=severity, message=message, details=details or {})
    db.add(ev)
    log.info("audit %s incident=%s plan=%s actor=%s: %s", event_type, incident_id, plan_id, actor, message)
    return ev


def transition(db: Session, incident: Incident, to_status: str, reason: str | None = None) -> None:
    if incident.status == to_status:
        return
    db.add(WorkflowTransition(incident_id=incident.id, from_status=incident.status, to_status=to_status, reason=reason))
    log.info("incident %s %s -> %s (%s)", incident.id, incident.status, to_status, reason or "")
    incident.status = to_status
    incident.status_reason = reason

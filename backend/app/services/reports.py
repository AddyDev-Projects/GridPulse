"""Report intake and the public view of a report's progress.

Citizen reports and explicitly requested demo reports go through `create_report`; nothing else
creates incidents. The public view exposes only coarse, non-sensitive fields.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.geo_context import MAX_SNAP_M
from app.db.base import utcnow
from app.geo.road_graph import RoadGraph
from app.models import Incident, ResponsePlan, WorkflowTransition
from app.services import audit
from app.simulation.repository import get_meta

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I/L/O/U


class ReportError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def new_reference(db: Session) -> str:
    for _ in range(10):
        ref = "GP-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if db.scalars(select(Incident.id).where(Incident.reference == ref)).first() is None:
            return ref
    raise RuntimeError("Could not allocate a unique report reference")


def create_report(
    db: Session,
    graph: RoadGraph,
    *,
    category: str,
    description: str,
    lat: float,
    lon: float,
    indicators: list[str],
    location_description: str | None,
    observed_at: datetime | None,
    source: str,
    client_key_hash: str | None = None,
) -> Incident:
    if not graph.in_bbox(lat, lon):
        s, w, n, e = graph.bbox
        raise ReportError(422, "outside_coverage",
                          f"The location is outside GridPulse's mapped Mangaluru coverage area "
                          f"({s:.3f}–{n:.3f}°N, {w:.3f}–{e:.3f}°E). Move the pin inside the shaded area.")
    now = utcnow()
    if observed_at is not None:
        if observed_at.tzinfo is None:
            raise ReportError(422, "invalid_time", "Observation time must include a timezone.")
        if observed_at > now + timedelta(minutes=5) or observed_at < now - timedelta(days=7):
            raise ReportError(422, "invalid_time", "Observation time must be within the last 7 days and not in the future.")
    meta = get_meta(db)
    snap = graph.nearest_segment(lat, lon, MAX_SNAP_M)
    inc = Incident(
        epoch=meta.epoch, scenario_key=meta.active_scenario, reporter_alias="Anonymous citizen"
        if source == "citizen_portal" else "Demo scenario (simulated report)",
        report_text=description, location_hint=location_description, location_description=location_description,
        category=category, lat=round(lat, 6), lon=round(lon, 6), segment_id=snap[0] if snap else None,
        indicators=list(indicators), observed_at=observed_at, source=source, city_id=meta.city_id,
        client_key_hash=client_key_hash, reference=new_reference(db), status="REPORTED",
    )
    db.add(inc)
    db.flush()
    db.add(WorkflowTransition(incident_id=inc.id, from_status=None, to_status="REPORTED",
                              reason="Citizen report received" if source == "citizen_portal" else "Demo report created"))
    audit.record(db, "report.submitted",
                 f"{'Citizen' if source == 'citizen_portal' else 'Simulated demo'} report {inc.reference} received "
                 f"({category.replace('_', ' ')}).", incident_id=inc.id,
                 actor="citizen:anonymous" if source == "citizen_portal" else "system:demo",
                 details={"category": category, "indicators": list(indicators), "source": source,
                          "snapped_segment": inc.segment_id, "length": len(description)})
    return inc


def public_status(inc: Incident) -> tuple[str, str]:
    s = inc.status
    if s == "DISMISSED":
        return "closed", "Reviewed and closed by an operator"
    if s == "REPORTED" and inc.run_started_at is None:
        return ("confirmed", "Confirmed by an operator; response being planned") if inc.verified_at else \
            ("received", "Received; waiting for operator review")
    if s == "EXECUTED":
        return "action_recorded", "Response actions recorded (simulation; nothing dispatched in the real world)"
    if s == "REJECTED":
        return "reviewed", "Reviewed by an operator; the proposed plan was not approved"
    if s in ("ESCALATED", "NO_SAFE_PLAN"):
        return "escalated", "Escalated for manual coordination"
    if inc.verified_at:
        return "confirmed", "Confirmed by an operator; response being planned"
    return "under_review", "Under review by operators"


def public_advisory(db: Session, inc: Incident) -> str | None:
    if inc.status != "EXECUTED":
        return None
    plan = db.scalars(select(ResponsePlan).where(ResponsePlan.incident_id == inc.id, ResponsePlan.status == "EXECUTED")).first()
    if plan is None:
        return None
    for a in plan.actions:
        if a.action_type == "issue_public_advisory":
            return a.params.get("message")
    if any(a.action_type == "close_roads" for a in plan.actions):
        return "Roads near this location are closed in the simulation. Avoid the area."
    return None

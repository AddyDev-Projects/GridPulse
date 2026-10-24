"""Public (anonymous) API: report a hazard, track a report, see verified incidents and closures.

No session is required and none is accepted for elevated rights here. Responses never include
agent deliberations, operator identities, audit details, raw coordinates of unverified reports,
or the simulated infrastructure model.
"""

from __future__ import annotations

from datetime import timedelta
from typing import get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip
from app.core.config import get_settings
from app.core.security import client_key
from app.db.base import utcnow
from app.db.session import get_db
from app.geo.road_graph import mangaluru_graph
from app.models import Incident, RoadClosure
from app.schemas.agents import IncidentCategory, SeverityIndicator
from app.schemas.api import (
    CityOut,
    ClosureOut,
    PublicIncident,
    PublicRecentIncident,
    PublicReportCreate,
    PublicReportReceipt,
    PublicReportStatus,
)
from app.services import ratelimit
from app.services.reports import ReportError, create_report, public_advisory, public_status
from app.simulation.repository import get_city, get_meta

router = APIRouter(prefix="/api/public", tags=["public"])

SENSITIVE_PUBLIC_CATEGORIES = {
    "police_assistance",
    "violence_assault",
    "homicide",
    "threat_weapon",
    "theft_robbery_burglary",
    "suspicious_activity",
}


# Closed by an operator: resolved after a response, or dismissed after review. Not shown as current reports.
INACTIVE_PUBLIC_STATUSES = ("RESOLVED", "DISMISSED")


def public_category(category: str | None) -> str:
    value = category or "other"
    return "police_assistance" if value in SENSITIVE_PUBLIC_CATEGORIES else value


@router.get("/city", response_model=CityOut)
def city(db: Session = Depends(get_db)) -> CityOut:
    c = get_city(db)
    g = mangaluru_graph()
    m = g.meta
    return CityOut(
        name=c.name, state=c.state, country=c.country, center=[c.center_lat, c.center_lon], coverage_bbox=c.coverage_bbox,
        graph={"source": m.get("source"), "license": m.get("license"), "osm_base_timestamp": m.get("osm_base_timestamp"),
               "nodes": len(g.nodes), "segments": len(g.segments), "sha256": m.get("sha256"),
               "weights": m.get("weights")},
        attribution="Road data © OpenStreetMap contributors (ODbL)",
        categories=list(get_args(IncidentCategory)), indicators=list(get_args(SeverityIndicator)),
    )


@router.post("/reports", response_model=PublicReportReceipt, status_code=201)
def submit_report(body: PublicReportCreate, request: Request, db: Session = Depends(get_db)) -> PublicReportReceipt:
    if body.website:  # honeypot field: humans never see it
        raise HTTPException(400, detail={"code": "rejected", "message": "Submission rejected."})
    s = get_settings()
    key = client_key("report", client_ip(request))
    for window, limit in ((timedelta(minutes=10), s.report_limit_per_10min), (timedelta(days=1), s.report_limit_per_day)):
        if ratelimit.count(db, "report", key, window) >= limit:
            retry = ratelimit.retry_after_s(db, "report", key, window)
            raise HTTPException(429, detail={"code": "rate_limited",
                                             "message": "Too many reports from this connection. Please try again later, "
                                                        "or call emergency services (112) if lives are at risk."},
                                headers={"Retry-After": str(retry)})
    try:
        inc = create_report(db, mangaluru_graph(), category=body.category, description=body.description, lat=body.lat,
                            lon=body.lon, indicators=list(body.indicators), location_description=body.location_description,
                            source="citizen_portal", client_key_hash=key)
    except ReportError as exc:
        db.rollback()
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message}) from None
    ratelimit.record(db, "report", key)
    db.commit()
    status, label = public_status(inc)
    seg = mangaluru_graph().segments.get(inc.segment_id) if inc.segment_id else None
    return PublicReportReceipt(reference=inc.reference, status=status, status_label=label, submitted_at=inc.created_at,
                               in_coverage=True, nearest_road=seg.label if seg else None)


@router.get("/reports/{reference}", response_model=PublicReportStatus)
def report_status(reference: str, db: Session = Depends(get_db)) -> PublicReportStatus:
    ref = reference.strip().upper()
    if not ref.startswith("GP-") or len(ref) != 11:
        raise HTTPException(404, detail={"code": "not_found", "message": "No report with that reference."})
    inc = db.scalars(select(Incident).where(Incident.reference == ref)).first()
    if inc is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "No report with that reference."})
    status, label = public_status(inc)
    return PublicReportStatus(reference=inc.reference, category=public_category(inc.category), status=status,
                              status_label=label, submitted_at=inc.created_at, updated_at=inc.updated_at,
                              public_note=public_advisory(db, inc))


@router.get("/incidents", response_model=list[PublicIncident])
def public_incidents(db: Session = Depends(get_db)) -> list[PublicIncident]:
    """Only operator-verified or acted-on incidents from the last 7 days; coordinates rounded (~100 m)."""
    meta = get_meta(db)
    since = utcnow() - timedelta(days=7)
    rows = db.scalars(select(Incident).where(
        Incident.city_id == meta.city_id, Incident.created_at >= since, Incident.status.not_in(INACTIVE_PUBLIC_STATUSES),
        (Incident.verified_at.is_not(None)) | (Incident.status == "EXECUTED"),
    ).order_by(Incident.updated_at.desc()).limit(200)).all()
    out = []
    for inc in rows:
        status, label = public_status(inc)
        out.append(PublicIncident(reference=inc.reference, category=public_category(inc.category), status=status,
                                  status_label=label, lat=round(inc.lat, 3), lon=round(inc.lon, 3),
                                  advisory=public_advisory(db, inc), verified=inc.verified_at is not None,
                                  updated_at=inc.updated_at, simulated_scenario=inc.source == "demo_scenario"))
    return out


@router.get("/recent-incidents", response_model=list[PublicRecentIncident])
def recent_public_incidents(db: Session = Depends(get_db)) -> list[PublicRecentIncident]:
    """Return the five newest *active* citizen reports with only coarse, public-safe fields.

    Eligibility is filtered in the query, before ordering and the limit, so a resolved or dismissed
    report never takes a slot: older active reports fill the list instead.
    """
    meta = get_meta(db)
    rows = db.scalars(
        select(Incident)
        .where(Incident.city_id == meta.city_id, Incident.source == "citizen_portal",
               Incident.reference.is_not(None), Incident.status.not_in(INACTIVE_PUBLIC_STATUSES))
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .limit(5)
    ).all()
    graph = mangaluru_graph()
    result: list[PublicRecentIncident] = []
    for inc in rows:
        category = inc.category or "other"
        sensitive = category in SENSITIVE_PUBLIC_CATEGORIES
        segment = graph.segments.get(inc.segment_id) if inc.segment_id else None
        location = "Mangaluru area" if sensitive else (f"Near {segment.label}" if segment else "Mangaluru area")
        public_category_value = public_category(category)
        status, label = public_status(inc)
        result.append(PublicRecentIncident(
            reference=inc.reference,
            category=public_category_value,
            location=location,
            reported_at=inc.created_at,
            status=status,
            status_label=label,
        ))
    return result


@router.get("/closures", response_model=list[ClosureOut])
def public_closures(db: Session = Depends(get_db)) -> list[ClosureOut]:
    meta = get_meta(db)
    g = mangaluru_graph()
    rows = db.scalars(select(RoadClosure).where(RoadClosure.city_id == meta.city_id, RoadClosure.lifted_at.is_(None))).all()
    return [ClosureOut(segment_id=r.segment_id, name=g.segments[r.segment_id].label, kind=r.kind, source=r.source,
                       reason="Simulated closure" if r.source == "scenario" else "Closed by an approved (simulated) plan",
                       created_at=r.created_at, geometry=[list(p) for p in g.segments[r.segment_id].geometry])
            for r in rows if r.segment_id in g.segments]


@router.get("/map/segments")
def road_segments(south: float = Query(ge=-90, le=90), west: float = Query(ge=-180, le=180),
                  north: float = Query(ge=-90, le=90), east: float = Query(ge=-180, le=180)) -> dict:
    """Road-graph segments in a small bounding box, so the map can show exactly what the router uses."""
    if north <= south or east <= west or (north - south) > 0.03 or (east - west) > 0.03:
        raise HTTPException(422, detail={"code": "bbox_too_large",
                                         "message": "Zoom in further (bounding box must be under 0.03° per side)."})
    g = mangaluru_graph()
    ids = g.segments_in_bbox(south, west, north, east, limit=3000)
    return {"segments": [{"id": sid, "name": g.segments[sid].label, "highway": g.segments[sid].highway,
                          "oneway": g.segments[sid].oneway, "geometry": [list(p) for p in g.segments[sid].geometry]}
                         for sid in ids], "truncated": len(ids) >= 3000,
            "attribution": "© OpenStreetMap contributors (ODbL)"}

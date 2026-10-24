"""Operator API. Every route requires an authenticated operator session (and CSRF on writes);
scenario control additionally requires the admin role. Health/readiness are the only open routes here."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import Principal, require_admin, require_operator
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.session import get_db
from app.geo.road_graph import mangaluru_graph
from app.models import (
    AgentProposal,
    AgentRun,
    Approval,
    AuditEvent,
    Incident,
    Operator,
    PlanRevision,
    ResponsePlan,
    RoadClosure,
    SimulationExecution,
    SimulationRoute,
    ValidationRun,
)
from app.orchestration.coordinator import run_workflow
from app.schemas.api import (
    ActionOut,
    AgentRunOut,
    ApprovalOut,
    AuditOut,
    ClosureOut,
    Decision,
    ExecutionOut,
    IncidentDetail,
    IncidentOut,
    IncidentSummary,
    NoteBody,
    OverlayNode,
    OverlayOut,
    PlanOut,
    PoiOut,
    ProposalOut,
    ReasonBody,
    RevisionOut,
    RouteOut,
    ScenarioLoad,
    ScenarioOut,
    SimulationStateOut,
    TransitionOut,
    ValidationRunOut,
    WorkflowOut,
)
from app.services import audit, decisions
from app.services.plans import latest_validation
from app.services.reports import create_report
from app.simulation.infrastructure import zone_bounds
from app.simulation.repository import (
    NO_SCENARIO,
    ScenarioError,
    clear_simulated_state,
    get_meta,
    load_scenario,
    load_snapshot,
    scenario_report,
)
from app.simulation.scenarios import SCENARIOS
from app.validation.actions import ACTION_TYPES

health_router = APIRouter(prefix="/api", tags=["health"])
router = APIRouter(prefix="/api", tags=["operator"], dependencies=[Depends(require_operator)])

TERMINAL = {"EXECUTED", "REJECTED", "ESCALATED", "NO_SAFE_PLAN", "FAILED", "DISMISSED"}


def _actor(p: Principal) -> str:
    return f"operator:{p.operator.username}"


def _scenario_out(key: str) -> ScenarioOut | None:
    s = SCENARIOS.get(key)
    if s is None:
        return None
    return ScenarioOut(key=s.key, code=s.code, title=s.title, summary=s.summary, expected_outcome=s.expected_outcome,
                       lat=s.lat, lon=s.lon, category=s.category, report_text=s.report_text)


def _get_incident(db: Session, incident_id: uuid.UUID, *, lock: bool = False) -> Incident:
    stmt = select(Incident).where(Incident.id == incident_id)
    if lock:
        stmt = stmt.with_for_update()
    inc = db.scalars(stmt).first()
    if inc is None:
        raise HTTPException(404, detail={"code": "incident_not_found", "message": "Incident not found."})
    return inc


def _get_plan(db: Session, plan_id: uuid.UUID) -> ResponsePlan:
    plan = db.get(ResponsePlan, plan_id)
    if plan is None:
        raise HTTPException(404, detail={"code": "plan_not_found", "message": "Plan not found."})
    return plan


def _geom(seg_ids: list[str]) -> list[list[list[float]]] | None:
    g = mangaluru_graph()
    if not all(s in g.segments for s in seg_ids):
        return None
    return [[list(p) for p in g.segments[s].geometry] for s in seg_ids]


def _route_geom(node_path: list[str], edge_path: list[str]) -> list[list[float]] | None:
    g = mangaluru_graph()
    if not edge_path or not all(e in g.segments for e in edge_path):
        return None
    return [list(p) for p in g.path_geometry(node_path, edge_path)]


def _target_name(target: str) -> str | None:
    g = mangaluru_graph()
    if target in g.segments:
        return g.segments[target].label
    if target in g.nodes:
        return g.node_label(target)
    return None


def _plan_out(db: Session, plan: ResponsePlan, current_state_version: int) -> PlanOut:
    vrun = latest_validation(db, plan.id)
    rev = db.scalars(select(PlanRevision).where(PlanRevision.to_plan_id == plan.id)).first()
    approvals = db.scalars(select(Approval).where(Approval.plan_id == plan.id).order_by(Approval.created_at)).all()
    execution = db.scalars(select(SimulationExecution).where(SimulationExecution.plan_id == plan.id)).first()
    inc = db.get(Incident, plan.incident_id)

    blockers: list[str] = []
    if inc is not None and inc.city_id is None:
        blockers.append("Legacy fictional-city plan (read-only).")
    if plan.status != "AWAITING_APPROVAL":
        blockers.append(f"Plan status is {plan.status}.")
    if vrun is None or not vrun.passed:
        blockers.append("Plan has no passing validation.")
    elif vrun.state_version != current_state_version:
        blockers.append(f"Validated against city state v{vrun.state_version}; the city is now v{current_state_version}. "
                        "Revalidate first.")
    actions = []
    for a in plan.actions:
        geometry = segments_geometry = None
        if a.action_type in ("designate_response_route",):
            geometry = _route_geom(a.params.get("node_path", []), a.params.get("edge_path", []))
        elif a.action_type in ("close_roads", "set_roads_caution"):
            segments_geometry = _geom(a.params.get("segment_ids", []))
        actions.append(ActionOut(seq=a.seq, action_type=a.action_type,
                                 label=ACTION_TYPES[a.action_type].label if a.action_type in ACTION_TYPES else a.action_type,
                                 target_id=a.target_id, target_name=_target_name(a.target_id), params=a.params,
                                 impact=a.impact, proposed_by=a.proposed_by, rationale=a.rationale,
                                 geometry=geometry, segments_geometry=segments_geometry))
    return PlanOut(
        id=plan.id, incident_id=plan.incident_id, version=plan.version, parent_plan_id=plan.parent_plan_id,
        status=plan.status, based_on_state_version=plan.based_on_state_version, content_hash=plan.content_hash,
        summary=plan.summary, rationale=plan.rationale, created_at=plan.created_at, actions=actions,
        latest_validation=ValidationRunOut.model_validate(vrun) if vrun else None,
        revision_from_parent=RevisionOut.model_validate(rev) if rev else None,
        approvals=[ApprovalOut.model_validate(a) for a in approvals],
        execution=ExecutionOut.model_validate(execution) if execution else None,
        current_state_version=current_state_version, approvable=not blockers, approval_blockers=blockers,
    )


def _route_out(r: SimulationRoute) -> RouteOut:
    out = RouteOut.model_validate(r)
    out.geometry = _route_geom(r.node_path, r.edge_path) or []
    return out


# ---- health (open) ---------------------------------------------------------------------------
@health_router.get("/health")
def health() -> dict:
    return {"status": "ok", "simulation_only": True}


@health_router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    meta = get_meta(db)
    g = mangaluru_graph()
    return {"status": "ready", "database": "ok", "migration": version, "city": "Mangaluru",
            "road_graph": {"segments": len(g.segments), "sha256": g.meta.get("sha256", "")[:12]},
            "state_version": meta.state_version}


# ---- simulation & scenarios ------------------------------------------------------------------
@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios() -> list[ScenarioOut]:
    return [_scenario_out(k) for k in SCENARIOS]


@router.post("/simulation/scenarios/{key}/load", response_model=SimulationStateOut)
def load_demo_scenario(key: str, body: ScenarioLoad, principal: Principal = Depends(require_admin),
                       db: Session = Depends(get_db)) -> SimulationStateOut:
    try:
        meta = load_scenario(db, key)
    except ScenarioError as exc:
        db.rollback()
        raise HTTPException(422, detail={"code": "scenario_error", "message": str(exc)}) from None
    sc = SCENARIOS[key]
    audit.record(db, "simulation.scenario_loaded",
                 f"Demo scenario {sc.code} ({sc.title}) loaded. SIMULATED sensors and closures; city state "
                 f"v{meta.state_version}; earlier plans are now stale.", actor=_actor(principal),
                 details={"scenario": key, "epoch": meta.epoch})
    if body.create_report:
        r = scenario_report(sc)
        inc = create_report(db, mangaluru_graph(), category=r["category"], description=r["description"], lat=r["lat"],
                            lon=r["lon"], indicators=r["indicators"], location_description=r["location_description"],
                            observed_at=None, source="demo_scenario")
        audit.record(db, "simulation.demo_report", f"Simulated demo report {inc.reference} created for scenario {sc.code}.",
                     incident_id=inc.id, actor=_actor(principal))
    db.commit()
    return simulation_state(db)


@router.post("/simulation/clear", response_model=SimulationStateOut)
def clear_simulation(principal: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> SimulationStateOut:
    meta = clear_simulated_state(db, f"Cleared by {principal.operator.username}")
    audit.record(db, "simulation.cleared", f"Simulated state cleared (closures lifted, overlay reset); city state "
                 f"v{meta.state_version}.", actor=_actor(principal))
    db.commit()
    return simulation_state(db)


@router.get("/simulation/state", response_model=SimulationStateOut)
def simulation_state(db: Session = Depends(get_db)) -> SimulationStateOut:
    meta = get_meta(db)
    snapshot = load_snapshot(db, meta)
    alerts = [{"id": n.id, "name": n.name, "kind": n.kind, "status": n.status}
              for n in snapshot.nodes.values()
              if n.status in ("dark", "isolated", "maintenance", "degraded") or (n.kind == "sensor" and n.status != "offline")]
    routes = db.scalars(select(SimulationRoute).where(SimulationRoute.active.is_(True), SimulationRoute.epoch == meta.epoch)).all()
    by_status = dict(db.execute(select(Incident.status, func.count()).where(Incident.city_id == meta.city_id)
                                .group_by(Incident.status)).all())
    pending = db.scalar(select(func.count()).select_from(ResponsePlan).join(Incident)
                        .where(ResponsePlan.status.in_(("AWAITING_APPROVAL", "APPROVED", "STALE")),
                               Incident.city_id == meta.city_id)) or 0
    return SimulationStateOut(
        city="Mangaluru", state_version=meta.state_version, epoch=meta.epoch,
        active_scenario=_scenario_out(meta.active_scenario) if meta.active_scenario != NO_SCENARIO else None,
        updated_at=meta.updated_at, closures=sum(1 for v in snapshot.road_status.values() if v == "closed"),
        alerts=sorted(alerts, key=lambda a: a["id"]), active_routes=[_route_out(r) for r in routes],
        incidents_by_status=by_status, pending_approvals=pending,
        new_reports=by_status.get("REPORTED", 0), llm_mode=get_settings().llm_provider,
    )


@router.get("/simulation/overlay", response_model=OverlayOut)
def simulation_overlay(db: Session = Depends(get_db)) -> OverlayOut:
    meta = get_meta(db)
    snapshot = load_snapshot(db, meta)
    g = snapshot.graph
    zones = [{"id": n.id, "name": n.name, "status": n.status, "bounds": list(zone_bounds(g, n.id)), "simulated": True}
             for n in snapshot.nodes.values() if n.kind == "substation"]
    nodes = [OverlayNode(id=n.id, kind=n.kind, name=n.name, zone=n.zone, lat=n.lat, lon=n.lon, status=n.status,
                         simulated=bool(n.attributes.get("simulated", True)), attributes=n.attributes)
             for n in snapshot.nodes.values() if n.kind in ("signal", "sensor") and n.status != "offline"]
    closures = db.scalars(select(RoadClosure).where(RoadClosure.city_id == meta.city_id, RoadClosure.lifted_at.is_(None))).all()
    routes = db.scalars(select(SimulationRoute).where(SimulationRoute.active.is_(True), SimulationRoute.epoch == meta.epoch)).all()
    return OverlayOut(
        nodes=nodes, zones=zones,
        closures=[ClosureOut(segment_id=c.segment_id, name=g.segments[c.segment_id].label, kind=c.kind, source=c.source,
                             reason=c.reason, created_at=c.created_at,
                             geometry=[list(p) for p in g.segments[c.segment_id].geometry])
                  for c in closures if c.segment_id in g.segments],
        active_routes=[_route_out(r) for r in routes],
        pois=[PoiOut(id=p.id, kind=p.kind, name=p.label, lat=p.lat, lon=p.lon) for p in g.pois],
        state_version=meta.state_version,
        simulated_layers=["feeder zones", "signal power model", "sensors", "closures", "routes"],
    )


# ---- incidents -------------------------------------------------------------------------------
@router.get("/incidents", response_model=list[IncidentSummary])
def list_incidents(scope: str = Query("current", pattern="^(current|all)$"), db: Session = Depends(get_db)) -> list[IncidentSummary]:
    meta = get_meta(db)
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(200)
    if scope == "current":
        stmt = stmt.where(Incident.city_id == meta.city_id)
    out = []
    for inc in db.scalars(stmt):
        plan = db.scalars(select(ResponsePlan).where(ResponsePlan.incident_id == inc.id)
                          .order_by(ResponsePlan.version.desc()).limit(1)).first()
        s = IncidentSummary.model_validate(inc)
        s.is_current_epoch = inc.epoch == meta.epoch
        s.legacy = inc.city_id is None
        if plan:
            s.current_plan_id, s.current_plan_version, s.current_plan_status = plan.id, plan.version, plan.status
        out.append(s)
    return out


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> IncidentDetail:
    inc = _get_incident(db, incident_id)
    meta = get_meta(db)
    runs = db.scalars(select(AgentRun).where(AgentRun.incident_id == inc.id).order_by(AgentRun.started_at)).all()
    triage = next((r for r in runs if r.step == "triage" and r.status == "succeeded"), None)
    ev = next((r for r in runs if r.step == "evidence" and r.status == "succeeded"), None)
    proposals = db.scalars(select(AgentProposal).where(AgentProposal.incident_id == inc.id)
                           .order_by(AgentProposal.created_at)).all()
    verifier = db.get(Operator, inc.verified_by_id) if inc.verified_by_id else None
    dismisser = db.get(Operator, inc.dismissed_by_id) if inc.dismissed_by_id else None
    hazard_geometry = []
    if ev and ev.output and ev.output.get("geo"):
        hazard_geometry = _geom(ev.output["geo"].get("hazard_segment_ids", [])) or []
    return IncidentDetail(
        incident=IncidentOut.model_validate(inc),
        assessment=triage.output if triage else None,
        assessment_engine=triage.engine if triage else None,
        assessment_fallback_reason=triage.fallback_reason if triage else None,
        evidence=ev.output if ev and inc.city_id is not None else None,
        proposals=[ProposalOut.model_validate(p) for p in proposals],
        is_current_epoch=inc.epoch == meta.epoch, legacy=inc.city_id is None,
        verified_by=verifier.username if verifier else None, dismissed_by=dismisser.username if dismisser else None,
        hazard_geometry=hazard_geometry,
    )


@router.post("/incidents/{incident_id}/verify", response_model=IncidentOut)
def verify_incident(incident_id: uuid.UUID, body: NoteBody, principal: Principal = Depends(require_operator),
                    db: Session = Depends(get_db)) -> Incident:
    """Operator confirms the hazard on record (e.g. after a call-back or field check). Counts as evidence."""
    inc = _get_incident(db, incident_id, lock=True)
    if inc.city_id is None or inc.status != "REPORTED" or inc.run_started_at is not None:
        raise HTTPException(409, detail={"code": "invalid_state",
                                         "message": "Only new reports, before the agents run, can be confirmed."})
    if inc.verified_at is not None:
        raise HTTPException(409, detail={"code": "already_verified", "message": "This report is already confirmed."})
    inc.verified_at, inc.verified_by_id, inc.verification_note = utcnow(), principal.operator.id, body.note
    audit.record(db, "report.verified", f"{principal.operator.username} confirmed the reported hazard on record.",
                 incident_id=inc.id, actor=_actor(principal), severity="success", details={"note": body.note})
    db.commit()
    return inc


@router.post("/incidents/{incident_id}/dismiss", response_model=IncidentOut)
def dismiss_incident(incident_id: uuid.UUID, body: ReasonBody, principal: Principal = Depends(require_operator),
                     db: Session = Depends(get_db)) -> Incident:
    inc = _get_incident(db, incident_id, lock=True)
    if inc.city_id is None or inc.status != "REPORTED" or inc.run_started_at is not None:
        raise HTTPException(409, detail={"code": "invalid_state", "message": "Only new reports can be dismissed."})
    inc.dismissed_at, inc.dismissed_by_id, inc.dismissal_reason = utcnow(), principal.operator.id, body.reason
    audit.transition(db, inc, "DISMISSED", f"Dismissed by {principal.operator.username}")
    inc.run_finished_at = utcnow()
    audit.record(db, "report.dismissed", f"{principal.operator.username} dismissed the report.", incident_id=inc.id,
                 actor=_actor(principal), severity="warning", details={"reason": body.reason})
    db.commit()
    return inc


@router.post("/incidents/{incident_id}/run", response_model=IncidentOut, status_code=202)
def run_incident(incident_id: uuid.UUID, background: BackgroundTasks, principal: Principal = Depends(require_operator),
                 db: Session = Depends(get_db)) -> Incident:
    inc = _get_incident(db, incident_id, lock=True)
    meta = get_meta(db)
    if inc.city_id is None:
        raise HTTPException(409, detail={"code": "legacy_read_only", "message": "Legacy incidents cannot be processed."})
    if inc.epoch != meta.epoch:
        raise HTTPException(409, detail={"code": "previous_run",
                                         "message": "This report predates the current simulated state (scenario was "
                                                    "loaded or cleared since). Ask the citizen to resubmit."})
    if inc.run_started_at is not None or inc.status != "REPORTED":
        raise HTTPException(409, detail={"code": "already_started", "message": "The workflow for this report has already run."})
    inc.run_started_at = utcnow()
    audit.record(db, "workflow.requested", f"{principal.operator.username} started the agent workflow.",
                 incident_id=inc.id, actor=_actor(principal))
    db.commit()
    background.add_task(run_workflow, inc.id)
    return inc


@router.get("/incidents/{incident_id}/workflow", response_model=WorkflowOut)
def get_workflow(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowOut:
    inc = _get_incident(db, incident_id)
    meta = get_meta(db)
    runs = db.scalars(select(AgentRun).where(AgentRun.incident_id == inc.id).order_by(AgentRun.started_at)).all()
    plans = db.scalars(select(ResponsePlan).where(ResponsePlan.incident_id == inc.id).order_by(ResponsePlan.version)).all()
    revisions = db.scalars(select(PlanRevision).where(PlanRevision.incident_id == inc.id)
                           .order_by(PlanRevision.revision_number)).all()
    return WorkflowOut(
        incident=IncidentOut.model_validate(inc),
        transitions=[TransitionOut.model_validate(t) for t in inc.transitions],
        agent_runs=[AgentRunOut.model_validate(r) for r in runs],
        plans=[_plan_out(db, p, meta.state_version) for p in plans],
        revisions=[RevisionOut.model_validate(r) for r in revisions],
        running=inc.run_started_at is not None and inc.run_finished_at is None and inc.status not in TERMINAL,
        max_revisions=get_settings().max_revisions,
    )


@router.get("/incidents/{incident_id}/plans", response_model=list[PlanOut])
def list_plans(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> list[PlanOut]:
    _get_incident(db, incident_id)
    meta = get_meta(db)
    plans = db.scalars(select(ResponsePlan).where(ResponsePlan.incident_id == incident_id).order_by(ResponsePlan.version)).all()
    return [_plan_out(db, p, meta.state_version) for p in plans]


@router.get("/incidents/{incident_id}/audit", response_model=list[AuditOut])
def incident_audit(incident_id: uuid.UUID, db: Session = Depends(get_db)) -> list[AuditEvent]:
    _get_incident(db, incident_id)
    return list(db.scalars(select(AuditEvent).where(AuditEvent.incident_id == incident_id).order_by(AuditEvent.id)))


@router.get("/audit", response_model=list[AuditOut])
def recent_audit(limit: int = Query(25, ge=1, le=200), db: Session = Depends(get_db)) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)))


# ---- plans -----------------------------------------------------------------------------------
@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: uuid.UUID, db: Session = Depends(get_db)) -> PlanOut:
    return _plan_out(db, _get_plan(db, plan_id), get_meta(db).state_version)


@router.get("/plans/{plan_id}/validation", response_model=list[ValidationRunOut])
def plan_validation(plan_id: uuid.UUID, db: Session = Depends(get_db)) -> list:
    _get_plan(db, plan_id)
    return list(db.scalars(select(ValidationRun).where(ValidationRun.plan_id == plan_id).order_by(ValidationRun.created_at)))


def _decision_error(exc: decisions.DecisionError) -> HTTPException:
    return HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/plans/{plan_id}/approve", response_model=PlanOut)
def approve_plan(plan_id: uuid.UUID, body: Decision, principal: Principal = Depends(require_operator),
                 db: Session = Depends(get_db)) -> PlanOut:
    try:
        decisions.approve(db, plan_id, principal.operator, body.content_hash, body.plan_version, body.note)
    except decisions.DecisionError as exc:
        raise _decision_error(exc) from exc
    return get_plan(plan_id, db)


@router.post("/plans/{plan_id}/reject", response_model=PlanOut)
def reject_plan(plan_id: uuid.UUID, body: Decision, principal: Principal = Depends(require_operator),
                db: Session = Depends(get_db)) -> PlanOut:
    try:
        decisions.reject(db, plan_id, principal.operator, body.content_hash, body.plan_version, body.note)
    except decisions.DecisionError as exc:
        raise _decision_error(exc) from exc
    return get_plan(plan_id, db)


@router.post("/plans/{plan_id}/revalidate", response_model=PlanOut)
def revalidate_plan(plan_id: uuid.UUID, principal: Principal = Depends(require_operator),
                    db: Session = Depends(get_db)) -> PlanOut:
    try:
        decisions.revalidate(db, plan_id, principal.operator)
    except decisions.DecisionError as exc:
        raise _decision_error(exc) from exc
    return get_plan(plan_id, db)


@router.post("/plans/{plan_id}/execute", response_model=ExecutionOut)
def execute_plan(plan_id: uuid.UUID, principal: Principal = Depends(require_operator),
                 db: Session = Depends(get_db)) -> SimulationExecution:
    try:
        return decisions.execute(db, plan_id, principal.operator)
    except decisions.DecisionError as exc:
        raise _decision_error(exc) from exc


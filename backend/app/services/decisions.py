"""Operator decisions and simulated execution. All authorization is enforced here, server-side.

Invariants
- Approval binds (plan id, plan version, content hash, city state_version).
- Only an AWAITING_APPROVAL plan whose latest passing validation matches the current state can be approved.
- Execution requires an active approval whose hash and state_version still match, a fresh
  pre-execution validation, and serializes on the simulation_meta row lock.
- UNIQUE(simulation_executions.plan_id) makes a duplicate execution impossible.
- Any failure rolls back the whole state change; the audit trail records the blocked attempt.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Approval, Incident, Operator, ResponsePlan, SimulationExecution, SimulationRoute
from app.services import audit
from app.services.plans import action_specs, content_hash, incident_context, incident_traffic, latest_validation, store_validation
from app.simulation.repository import get_meta, load_snapshot, persist_changes
from app.simulation.state import apply_actions
from app.validation.validator import validate_plan

log = logging.getLogger("gridpulse.decisions")


@dataclass
class DecisionError(Exception):
    status_code: int
    code: str
    message: str


def _lock_plan(db: Session, plan_id: uuid.UUID) -> ResponsePlan:
    plan = db.scalars(select(ResponsePlan).where(ResponsePlan.id == plan_id).with_for_update()).first()
    if plan is None:
        raise DecisionError(404, "plan_not_found", "Plan not found.")
    return plan


def _incident(db: Session, plan: ResponsePlan) -> Incident:
    inc = db.scalars(select(Incident).where(Incident.id == plan.incident_id).with_for_update()).one()
    if inc.city_id is None:
        raise DecisionError(409, "legacy_read_only",
                            "This plan belongs to a legacy fictional-city incident and is kept read-only for history.")
    return inc


def _check_hash(plan: ResponsePlan, expected_hash: str, expected_version: int) -> None:
    if plan.version != expected_version or plan.content_hash != expected_hash:
        raise DecisionError(409, "plan_mismatch",
                            f"You reviewed a different plan (v{expected_version}); the current plan is v{plan.version}. "
                            "Review the current version before deciding.")
    if content_hash(plan.incident_id, plan.version, action_specs(plan)) != plan.content_hash:
        raise DecisionError(409, "plan_integrity", "Stored plan actions do not match their content hash.")


def _mark_stale(db: Session, plan: ResponsePlan, incident: Incident, reason: str) -> None:
    for appr in db.scalars(select(Approval).where(Approval.plan_id == plan.id, Approval.status == "active")):
        appr.status = "invalidated"
        appr.invalidated_reason = reason
    plan.status = "STALE"
    audit.transition(db, incident, "AWAITING_APPROVAL", "Plan is stale; revalidation required")
    audit.record(db, "approval.invalidated", reason, incident_id=incident.id, plan_id=plan.id,
                 actor="system", severity="warning")


def approve(db: Session, plan_id: uuid.UUID, op: Operator, expected_hash: str, expected_version: int, note: str | None) -> Approval:
    operator = op.username
    plan = _lock_plan(db, plan_id)
    incident = _incident(db, plan)
    meta = get_meta(db)
    if plan.status != "AWAITING_APPROVAL":
        raise DecisionError(409, "invalid_state", f"Plan v{plan.version} is {plan.status}; only plans awaiting approval can be approved.")
    _check_hash(plan, expected_hash, expected_version)
    vrun = latest_validation(db, plan.id)
    if vrun is None or not vrun.passed:
        raise DecisionError(409, "not_validated", "Plan has no passing validation.")
    if vrun.state_version != meta.state_version:
        reason = (f"City state changed (v{vrun.state_version} → v{meta.state_version}) since plan v{plan.version} "
                  "was validated. Revalidate before approving.")
        _mark_stale(db, plan, incident, reason)
        db.commit()
        raise DecisionError(409, "stale_plan", reason)

    appr = Approval(plan_id=plan.id, decision="approved", operator=operator, operator_id=op.id, note=note, plan_version=plan.version,
                    plan_content_hash=plan.content_hash, state_version=meta.state_version, status="active")
    db.add(appr)
    plan.status = "APPROVED"
    audit.transition(db, incident, "APPROVED", f"Operator {operator} approved plan v{plan.version}")
    audit.record(db, "approval.granted", f"{operator} approved plan v{plan.version} (hash {plan.content_hash[:12]}…).",
                 incident_id=incident.id, plan_id=plan.id, actor=f"operator:{operator}", severity="success",
                 details={"state_version": meta.state_version, "note": note})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DecisionError(409, "duplicate_approval", "This plan already has an active approval.") from exc
    return appr


def reject(db: Session, plan_id: uuid.UUID, op: Operator, expected_hash: str, expected_version: int, note: str | None) -> Approval:
    operator = op.username
    plan = _lock_plan(db, plan_id)
    incident = _incident(db, plan)
    meta = get_meta(db)
    if plan.status not in ("AWAITING_APPROVAL", "STALE", "APPROVED"):
        raise DecisionError(409, "invalid_state", f"Plan v{plan.version} is {plan.status} and cannot be rejected.")
    _check_hash(plan, expected_hash, expected_version)
    for a in db.scalars(select(Approval).where(Approval.plan_id == plan.id, Approval.status == "active")):
        a.status = "invalidated"
        a.invalidated_reason = "Plan rejected by operator"
    rej = Approval(plan_id=plan.id, decision="rejected", operator=operator, operator_id=op.id, note=note, plan_version=plan.version,
                   plan_content_hash=plan.content_hash, state_version=meta.state_version, status="final")
    db.add(rej)
    plan.status = "REJECTED"
    incident.run_finished_at = incident.run_finished_at or utcnow()
    audit.transition(db, incident, "REJECTED", f"Operator {operator} rejected plan v{plan.version}")
    audit.record(db, "approval.rejected",
                 f"{operator} rejected plan v{plan.version}. No simulated actions were applied.",
                 incident_id=incident.id, plan_id=plan.id, actor=f"operator:{operator}", severity="warning",
                 details={"note": note, "state_version": meta.state_version})
    db.commit()
    return rej


def revalidate(db: Session, plan_id: uuid.UUID, op: Operator) -> ResponsePlan:
    operator = op.username
    """Re-run the validator against the current city state for a stale (or pending) plan."""
    plan = _lock_plan(db, plan_id)
    incident = _incident(db, plan)
    meta = get_meta(db)
    if plan.status not in ("STALE", "AWAITING_APPROVAL"):
        raise DecisionError(409, "invalid_state", f"Plan v{plan.version} is {plan.status}; it cannot be revalidated.")
    _, evidence = incident_context(db, incident.id)
    traffic_context = incident_traffic(db, incident.id)
    snapshot = load_snapshot(db, meta)
    result = validate_plan(action_specs(plan), snapshot, evidence, traffic=traffic_context)
    store_validation(db, plan, result, meta.state_version, "revalidate")
    if result.passed:
        plan.status = "AWAITING_APPROVAL"
        plan.based_on_state_version = meta.state_version
        audit.transition(db, incident, "AWAITING_APPROVAL", f"Plan v{plan.version} revalidated against state v{meta.state_version}")
        audit.record(db, "validation.completed",
                     f"{operator} revalidated plan v{plan.version} against state v{meta.state_version}: passed. Fresh approval required.",
                     incident_id=incident.id, plan_id=plan.id, actor=f"operator:{operator}", severity="success")
    else:
        plan.status = "INVALID"
        audit.transition(db, incident, "ESCALATED", f"Plan v{plan.version} is no longer valid against the current state")
        for f in result.blocking():
            audit.record(db, "validation.finding", f"[{f.rule_id}] {f.title}", incident_id=incident.id,
                         plan_id=plan.id, actor="validator", severity="error", details=f.as_dict())
        audit.record(db, "workflow.escalated",
                     f"Plan v{plan.version} failed revalidation; escalated for a new response.",
                     incident_id=incident.id, plan_id=plan.id, actor="validator", severity="error")
    db.commit()
    return plan


def _blocked(db: Session, plan: ResponsePlan, code: str, message: str, status: int = 409) -> DecisionError:
    audit.record(db, "execution.blocked", message, incident_id=plan.incident_id, plan_id=plan.id,
                 actor="system", severity="error", details={"code": code})
    db.commit()
    return DecisionError(status, code, message)


def execute(db: Session, plan_id: uuid.UUID, op: Operator) -> SimulationExecution:
    operator = op.username
    # Lock order: simulation_meta, then plan, then incident — serializes all executions.
    meta = get_meta(db, for_update=True)
    plan = _lock_plan(db, plan_id)
    incident = _incident(db, plan)

    existing = db.scalars(select(SimulationExecution).where(SimulationExecution.plan_id == plan.id)).first()
    if existing is not None:
        raise _blocked(db, plan, "already_executed", f"Plan v{plan.version} was already executed; duplicate request ignored.")
    if plan.status != "APPROVED":
        raise _blocked(db, plan, "not_approved", f"Plan v{plan.version} is {plan.status}; only approved plans can execute.")
    appr = db.scalars(select(Approval).where(Approval.plan_id == plan.id, Approval.decision == "approved",
                                             Approval.status == "active")).first()
    if appr is None:
        raise _blocked(db, plan, "no_approval", "No active approval for this plan version.")
    specs = action_specs(plan)
    if appr.plan_content_hash != plan.content_hash or appr.plan_version != plan.version \
            or content_hash(plan.incident_id, plan.version, specs) != plan.content_hash:
        appr.status = "invalidated"
        appr.invalidated_reason = "Plan content differs from what was approved"
        raise _blocked(db, plan, "approval_mismatch", "The approval does not cover this exact plan content.")
    if appr.state_version != meta.state_version:
        reason = (f"City state changed (v{appr.state_version} → v{meta.state_version}) after approval; "
                  "the approval no longer applies. Revalidate and approve again.")
        _mark_stale(db, plan, incident, reason)
        raise _blocked(db, plan, "stale_approval", reason)

    # Fresh deterministic validation immediately before mutating anything.
    _, evidence = incident_context(db, incident.id)
    traffic_context = incident_traffic(db, incident.id)
    snapshot = load_snapshot(db, meta)
    result = validate_plan(specs, snapshot, evidence, expected_state_version=appr.state_version,
                           traffic=traffic_context)
    store_validation(db, plan, result, meta.state_version, "pre_execution")
    if not result.passed:
        appr.status = "invalidated"
        appr.invalidated_reason = "Pre-execution validation failed"
        plan.status = "INVALID"
        raise _blocked(db, plan, "validation_failed",
                       "Pre-execution validation failed: " + "; ".join(f.title for f in result.blocking()))

    _, changes = apply_actions(snapshot, specs)
    before = meta.state_version
    try:
        persist_changes(db, changes, city_id=meta.city_id, epoch=meta.epoch, plan_id=plan.id)
        results = []
        for a in specs:
            affected = {a.target_id}
            if a.action_type in ("close_roads", "set_roads_caution"):
                affected = set(a.params["segment_ids"])
            elif a.action_type == "isolate_substation":
                affected |= set(snapshot.dependents_of(a.target_id))
            elif a.action_type == "transfer_signal_power":
                affected |= {a.params["from_feed"], a.params["to_feed"], a.target_id}
            entry = {"seq": a.seq, "action_type": a.action_type, "target_id": a.target_id, "status": "applied",
                     "changes": [c for c in changes if c["id"] in affected]}
            if a.action_type == "designate_response_route":
                p = a.params
                db.query(SimulationRoute).filter(SimulationRoute.active.is_(True),
                                                 SimulationRoute.purpose == p["purpose"],
                                                 SimulationRoute.destination_id == p["destination"]).update({"active": False})
                db.add(SimulationRoute(plan_id=plan.id, city_id=meta.city_id, epoch=meta.epoch, purpose=p["purpose"],
                                       name=p["name"][:160], origin_id=p["origin"], destination_id=p["destination"],
                                       edge_path=p["edge_path"], node_path=p["node_path"], length_m=p["length_m"],
                                       active=True))
                entry["changes"] = [{"entity": "route", "id": p["purpose"], "name": p["name"],
                                     "after": f"{p['length_m'] / 1000:.2f} km via {len(p['edge_path'])} segments"}]
            elif a.action_type in ("issue_public_advisory", "dispatch_field_verification"):
                entry["status"] = "recorded"
                entry["note"] = a.params.get("message") or a.params.get("question")
            results.append(entry)
        meta.state_version += 1
        meta.updated_at = utcnow()
        execution = SimulationExecution(plan_id=plan.id, approval_id=appr.id, status="succeeded",
                                        state_version_before=before, state_version_after=meta.state_version,
                                        results=results, executed_by=operator, executed_by_id=op.id,
                                        notes=f"{len(changes)} simulated state change(s). Nothing was dispatched in the real world.")
        db.add(execution)
        appr.status = "consumed"
        plan.status = "EXECUTED"
        incident.run_finished_at = utcnow()
        audit.transition(db, incident, "EXECUTED", f"Plan v{plan.version} applied to the simulation")
        audit.record(db, "execution.succeeded",
                     f"Plan v{plan.version} executed in simulation: {len(changes)} state change(s); "
                     f"city state v{before} → v{meta.state_version}.",
                     incident_id=incident.id, plan_id=plan.id, actor=f"operator:{operator}", severity="success",
                     details={"changes": changes})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DecisionError(409, "already_executed", "Plan was executed by a concurrent request.") from exc
    except Exception:
        db.rollback()
        log.exception("execution failed for plan %s", plan_id)
        audit.record(db, "execution.failed", "Execution failed and was rolled back; simulation state unchanged.",
                     incident_id=plan.incident_id, plan_id=plan.id, actor="system", severity="error")
        db.commit()
        raise
    return execution

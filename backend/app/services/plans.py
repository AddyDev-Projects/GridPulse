"""Plan persistence helpers: versioning, content hashing, validation-run storage."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun, PlanAction, ResponsePlan, ValidationFinding, ValidationRun
from app.schemas.agents import EvidenceAssessment, SentinelAssessment
from app.simulation.state import ActionSpec
from app.validation.validator import Finding, ValidationResult


def content_hash(incident_id: uuid.UUID, version: int, actions: list[ActionSpec]) -> str:
    payload = {
        "incident_id": str(incident_id),
        "version": version,
        "actions": [{"seq": a.seq, "type": a.action_type, "target": a.target_id, "params": a.params} for a in actions],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def action_specs(plan: ResponsePlan) -> list[ActionSpec]:
    return [ActionSpec(a.seq, a.action_type, a.target_id, dict(a.params), a.proposed_by, a.impact, a.rationale)
            for a in plan.actions]


def create_plan(
    db: Session,
    incident_id: uuid.UUID,
    version: int,
    actions: list[ActionSpec],
    *,
    state_version: int,
    summary: str,
    rationale: str | None = None,
    parent_plan_id: uuid.UUID | None = None,
    proposal_ids: dict[str, uuid.UUID] | None = None,
) -> ResponsePlan:
    plan = ResponsePlan(
        incident_id=incident_id, version=version, parent_plan_id=parent_plan_id, status="DRAFT",
        based_on_state_version=state_version, content_hash=content_hash(incident_id, version, actions),
        summary=summary, rationale=rationale,
    )
    for a in actions:
        plan.actions.append(PlanAction(
            seq=a.seq, action_type=a.action_type, target_id=a.target_id, params=a.params, impact=a.impact,
            proposed_by=a.proposed_by, rationale=a.rationale,
            proposal_id=(proposal_ids or {}).get(a.proposed_by),
        ))
    db.add(plan)
    db.flush()
    return plan


def store_validation(db: Session, plan: ResponsePlan, result: ValidationResult, state_version: int, trigger: str) -> ValidationRun:
    run = ValidationRun(
        plan_id=plan.id, state_version=state_version, trigger=trigger, passed=result.passed,
        blocking_count=result.count("blocking"), warning_count=result.count("warning"), info_count=result.count("info"),
    )
    for f in result.findings:
        run.findings.append(_finding_row(f))
    db.add(run)
    db.flush()
    return run


def append_finding(db: Session, run: ValidationRun, f: Finding) -> None:
    run.findings.append(_finding_row(f))
    if f.severity == "blocking":
        run.blocking_count += 1
        run.passed = False
    db.flush()


def _finding_row(f: Finding) -> ValidationFinding:
    return ValidationFinding(rule_id=f.rule_id, severity=f.severity, agent=f.agent, action_seq=f.action_seq,
                             title=f.title[:200], message=f.message, remediation=f.remediation,
                             evidence=f.evidence, can_proceed=f.can_proceed)


def latest_validation(db: Session, plan_id: uuid.UUID) -> ValidationRun | None:
    return db.scalars(
        select(ValidationRun).where(ValidationRun.plan_id == plan_id).order_by(ValidationRun.created_at.desc()).limit(1)
    ).first()


def incident_context(db: Session, incident_id: uuid.UUID) -> tuple[SentinelAssessment | None, EvidenceAssessment | None]:
    """The assessment and evidence the plans were built on, as persisted by the workflow."""
    runs = db.scalars(
        select(AgentRun).where(AgentRun.incident_id == incident_id, AgentRun.status == "succeeded",
                               AgentRun.step.in_(("triage", "evidence"))).order_by(AgentRun.started_at)
    ).all()
    assessment = evidence = None
    for r in runs:
        if r.step == "triage" and r.output:
            assessment = SentinelAssessment.model_validate(r.output)
        elif r.step == "evidence" and r.output:
            evidence = EvidenceAssessment.model_validate(r.output)
    return assessment, evidence

"""Incidents, agent activity, plans, validation, revisions, approvals and audit."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow

INCIDENT_STATUSES = (
    "REPORTED",
    "TRIAGED",
    "EVIDENCE_GATHERED",
    "PLANS_PROPOSED",
    "VALIDATING",
    "REVISION_REQUIRED",
    "VALIDATING_AGAIN",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXECUTED",
    "REJECTED",
    "ESCALATED",
    "NO_SAFE_PLAN",
    "FAILED",
    "DISMISSED",
)
INCIDENT_SOURCES = ("citizen_portal", "demo_scenario", "legacy")
PLAN_STATUSES = (
    "DRAFT",
    "REVISION_REQUIRED",
    "SUPERSEDED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "EXECUTED",
    "STALE",
    "ESCALATED",
    "INVALID",
)
SEVERITIES = ("blocking", "warning", "info")


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(f"status IN {INCIDENT_STATUSES}", name="status"),
        CheckConstraint("char_length(report_text) BETWEEN 10 AND 2000", name="report_length"),
        CheckConstraint(f"source IN {INCIDENT_SOURCES}", name="source"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180)", name="coordinates"),
        Index("ix_incidents_epoch_created", "epoch", "created_at"),
        Index("ix_incidents_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario_key: Mapped[str] = mapped_column(String(32), nullable=False)
    reporter_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    location_hint: Mapped[str | None] = mapped_column(String(120))
    location_id: Mapped[str | None] = mapped_column(ForeignKey("infrastructure_edges.id"))  # legacy only
    category: Mapped[str | None] = mapped_column(String(32))
    urgency: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="REPORTED")
    status_reason: Mapped[str | None] = mapped_column(Text)
    # Citizen-portal / Mangaluru fields (NULL on legacy fictional-city incidents)
    reference: Mapped[str | None] = mapped_column(String(16), unique=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="legacy", server_default="legacy")
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    segment_id: Mapped[str | None] = mapped_column(String(40))
    location_description: Mapped[str | None] = mapped_column(String(200))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indicators: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    client_key_hash: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id", ondelete="RESTRICT"))
    verification_note: Mapped[str | None] = mapped_column(String(500))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id", ondelete="RESTRICT"))
    dismissal_reason: Mapped[str | None] = mapped_column(String(500))
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    transitions: Mapped[list["WorkflowTransition"]] = relationship(
        back_populates="incident", order_by="WorkflowTransition.id", cascade="all, delete-orphan"
    )


class WorkflowTransition(Base, TimestampMixin):
    __tablename__ = "workflow_transitions"
    __table_args__ = (Index("ix_workflow_transitions_incident", "incident_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)

    incident: Mapped[Incident] = relationship(back_populates="transitions")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status"),
        Index("ix_agent_runs_incident", "incident_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    agent: Mapped[str] = mapped_column(String(24), nullable=False)
    step: Mapped[str] = mapped_column(String(48), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    engine: Mapped[str] = mapped_column(String(32), nullable=False)  # "llm:<provider>", "rules", "deterministic"
    model: Mapped[str | None] = mapped_column(String(80))
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AgentProposal(Base, TimestampMixin):
    __tablename__ = "agent_proposals"
    __table_args__ = (Index("ix_agent_proposals_incident", "incident_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    agent: Mapped[str] = mapped_column(String(24), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ResponsePlan(Base, TimestampMixin):
    __tablename__ = "response_plans"
    __table_args__ = (
        UniqueConstraint("incident_id", "version", name="uq_response_plans_incident_version"),
        CheckConstraint(f"status IN {PLAN_STATUSES}", name="status"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("response_plans.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    based_on_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    actions: Mapped[list["PlanAction"]] = relationship(
        order_by="PlanAction.seq", cascade="all, delete-orphan", lazy="selectin"
    )


class PlanAction(Base):
    __tablename__ = "plan_actions"
    __table_args__ = (
        UniqueConstraint("plan_id", "seq", name="uq_plan_actions_plan_seq"),
        CheckConstraint("impact IN ('low', 'medium', 'high')", name="impact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    target_id: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    impact: Mapped[str] = mapped_column(String(8), nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(24), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_proposals.id", ondelete="SET NULL"))
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


class ValidationRun(Base, TimestampMixin):
    __tablename__ = "validation_runs"
    __table_args__ = (Index("ix_validation_runs_plan", "plan_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blocking_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    findings: Mapped[list["ValidationFinding"]] = relationship(
        order_by="ValidationFinding.id", cascade="all, delete-orphan", lazy="selectin"
    )


class ValidationFinding(Base):
    __tablename__ = "validation_findings"
    __table_args__ = (CheckConstraint(f"severity IN {SEVERITIES}", name="severity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("validation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    agent: Mapped[str | None] = mapped_column(String(24))
    action_seq: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remediation: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    can_proceed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PlanRevision(Base, TimestampMixin):
    __tablename__ = "plan_revisions"
    __table_args__ = (Index("ix_plan_revisions_incident", "incident_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    from_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False)
    to_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str] = mapped_column(String(48), nullable=False)
    triggered_by: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="decision"),
        CheckConstraint("status IN ('active', 'invalidated', 'consumed', 'final')", name="status"),
        # At most one live approval per plan.
        Index(
            "uq_approvals_one_active_per_plan",
            "plan_id",
            unique=True,
            postgresql_where=text("decision = 'approved' AND status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    operator: Mapped[str] = mapped_column(String(80), nullable=False)  # username snapshot (free text on legacy rows)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id", ondelete="RESTRICT"))
    note: Mapped[str | None] = mapped_column(Text)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    invalidated_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuditEvent(Base, TimestampMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error', 'success')", name="severity"),
        Index("ix_audit_events_incident", "incident_id", "id"),
        Index("ix_audit_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("response_plans.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

"""Request and response models for the REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.agents import EvidenceAssessment, IncidentCategory, SentinelAssessment, SeverityIndicator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- requests --------------------------------------------------------------------------------
def _clean(v: str | None) -> str | None:
    if v is None:
        return v
    cleaned = "".join(ch for ch in v if ch.isprintable() or ch in "\n\t").strip()
    return cleaned or None


class PublicReportCreate(BaseModel):
    """Anonymous citizen report. No identity is collected."""

    model_config = ConfigDict(extra="forbid")

    category: IncidentCategory
    description: str = Field(min_length=10, max_length=2000)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    location_description: str | None = Field(default=None, max_length=200)
    observed_at: datetime | None = None
    indicators: list[SeverityIndicator] = Field(default_factory=list, max_length=8)
    website: str | None = Field(default=None, max_length=200, description="Honeypot. Must be empty.")

    @field_validator("description", "location_description")
    @classmethod
    def strip_controls(cls, v: str | None) -> str | None:
        return _clean(v)

    @field_validator("indicators")
    @classmethod
    def unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Decision(BaseModel):
    """Approve/reject. The operator comes from the authenticated session, never from the body."""

    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=500)


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_controls(cls, v: str | None) -> str | None:
        return _clean(v)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class ScenarioLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    create_report: bool = True


# ---- responses -------------------------------------------------------------------------------
class TransitionOut(ORM):
    id: int
    from_status: str | None
    to_status: str
    reason: str | None
    created_at: datetime


class IncidentOut(ORM):
    id: uuid.UUID
    reference: str | None
    source: str
    epoch: int
    scenario_key: str
    reporter_alias: str
    report_text: str
    location_hint: str | None
    location_id: str | None
    lat: float | None
    lon: float | None
    segment_id: str | None
    location_description: str | None
    observed_at: datetime | None
    indicators: list[str]
    category: str | None
    urgency: str | None
    status: str
    status_reason: str | None
    verified_at: datetime | None
    verification_note: str | None
    dismissed_at: datetime | None
    dismissal_reason: str | None
    run_started_at: datetime | None
    run_finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    city_id: int | None

    @property
    def is_legacy(self) -> bool:
        return self.city_id is None


class IncidentSummary(IncidentOut):
    current_plan_id: uuid.UUID | None = None
    current_plan_version: int | None = None
    current_plan_status: str | None = None
    is_current_epoch: bool = True
    legacy: bool = False


class AgentRunOut(ORM):
    id: uuid.UUID
    agent: str
    step: str
    revision_number: int
    status: str
    engine: str
    model: str | None
    fallback_used: bool
    fallback_reason: str | None
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class ProposalOut(ORM):
    id: uuid.UUID
    agent: str
    revision_number: int
    summary: str
    payload: dict[str, Any]
    created_at: datetime


class IncidentDetail(BaseModel):
    incident: IncidentOut
    assessment: SentinelAssessment | None
    assessment_engine: str | None
    assessment_fallback_reason: str | None
    evidence: EvidenceAssessment | None
    proposals: list[ProposalOut]
    is_current_epoch: bool
    legacy: bool
    verified_by: str | None = None
    dismissed_by: str | None = None
    hazard_geometry: list[list[list[float]]] = []


class ActionOut(ORM):
    seq: int
    action_type: str
    label: str = ""
    target_id: str
    target_name: str | None = None
    params: dict[str, Any]
    impact: str
    proposed_by: str
    rationale: str
    geometry: list[list[float]] | None = None  # route polyline from the graph edges actually validated
    segments_geometry: list[list[list[float]]] | None = None  # closures: one polyline per segment


class FindingOut(ORM):
    id: int
    rule_id: str
    severity: str
    agent: str | None
    action_seq: int | None
    title: str
    message: str
    remediation: str | None
    evidence: dict[str, Any]
    can_proceed: bool


class ValidationRunOut(ORM):
    id: uuid.UUID
    state_version: int
    trigger: str
    passed: bool
    blocking_count: int
    warning_count: int
    info_count: int
    created_at: datetime
    findings: list[FindingOut]


class ApprovalOut(ORM):
    id: uuid.UUID
    decision: str
    operator: str
    note: str | None
    plan_version: int
    plan_content_hash: str
    state_version: int
    status: str
    invalidated_reason: str | None
    created_at: datetime


class ExecutionOut(ORM):
    id: uuid.UUID
    plan_id: uuid.UUID
    status: str
    state_version_before: int
    state_version_after: int
    results: list[dict[str, Any]]
    executed_by: str
    executed_at: datetime
    notes: str | None


class RevisionOut(ORM):
    id: int
    from_plan_id: uuid.UUID
    to_plan_id: uuid.UUID
    revision_number: int
    strategy: str
    triggered_by: list[dict[str, Any]]
    alternatives: list[dict[str, Any]]
    diff: dict[str, Any]
    rationale: str
    created_at: datetime


class PlanOut(ORM):
    id: uuid.UUID
    incident_id: uuid.UUID
    version: int
    parent_plan_id: uuid.UUID | None
    status: str
    based_on_state_version: int
    content_hash: str
    summary: str
    rationale: str | None
    created_at: datetime
    actions: list[ActionOut]
    latest_validation: ValidationRunOut | None = None
    revision_from_parent: RevisionOut | None = None
    approvals: list[ApprovalOut] = []
    execution: ExecutionOut | None = None
    current_state_version: int
    approvable: bool = False
    approval_blockers: list[str] = []


class WorkflowOut(BaseModel):
    incident: IncidentOut
    transitions: list[TransitionOut]
    agent_runs: list[AgentRunOut]
    plans: list[PlanOut]
    revisions: list[RevisionOut]
    running: bool
    max_revisions: int


class AuditOut(ORM):
    id: int
    incident_id: uuid.UUID | None
    plan_id: uuid.UUID | None
    event_type: str
    actor: str
    severity: str
    message: str
    details: dict[str, Any]
    created_at: datetime


class OverlayNode(BaseModel):
    id: str
    kind: str
    name: str
    zone: str
    lat: float
    lon: float
    status: str
    simulated: bool
    attributes: dict[str, Any]


class ClosureOut(BaseModel):
    segment_id: str
    name: str
    kind: str
    source: str
    reason: str | None
    created_at: datetime
    geometry: list[list[float]]


class RouteOut(ORM):
    id: int
    plan_id: uuid.UUID
    purpose: str | None
    name: str
    origin_id: str
    destination_id: str
    edge_path: list[str]
    node_path: list[str]
    length_m: float
    created_at: datetime
    geometry: list[list[float]] = []


class PoiOut(BaseModel):
    id: str
    kind: str
    name: str
    lat: float
    lon: float
    source: str = "OpenStreetMap"


class CityOut(BaseModel):
    name: str
    state: str
    country: str
    center: list[float]
    coverage_bbox: list[float]
    graph: dict[str, Any]
    attribution: str
    categories: list[str]
    indicators: list[str]


class OverlayOut(BaseModel):
    """Map layers for operators. Feeder zones and power model are SIMULATED; roads and POIs are OSM."""

    nodes: list[OverlayNode]
    zones: list[dict[str, Any]]
    closures: list[ClosureOut]
    active_routes: list[RouteOut]
    pois: list[PoiOut]
    state_version: int
    simulated_layers: list[str]


class ScenarioOut(BaseModel):
    key: str
    code: str
    title: str
    summary: str
    expected_outcome: str
    lat: float
    lon: float
    category: str
    report_text: str


class SimulationStateOut(BaseModel):
    city: str
    state_version: int
    epoch: int
    active_scenario: ScenarioOut | None
    updated_at: datetime
    closures: int
    alerts: list[dict[str, Any]]
    active_routes: list[RouteOut]
    incidents_by_status: dict[str, int]
    pending_approvals: int
    new_reports: int
    llm_mode: str


class OperatorOut(BaseModel):
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    operator: OperatorOut
    csrf_token: str
    expires_at: datetime


class PublicReportReceipt(BaseModel):
    reference: str
    status: str
    status_label: str
    submitted_at: datetime
    in_coverage: bool
    nearest_road: str | None


class PublicReportStatus(BaseModel):
    reference: str
    category: str
    status: str
    status_label: str
    submitted_at: datetime
    updated_at: datetime
    public_note: str | None


class PublicIncident(BaseModel):
    reference: str
    category: str
    status: str
    status_label: str
    lat: float
    lon: float
    advisory: str | None
    verified: bool
    updated_at: datetime
    simulated_scenario: bool


class PublicRecentIncident(BaseModel):
    """Minimal, sanitized citizen-facing summary of a recently reported incident."""

    reference: str
    category: str
    location: str
    reported_at: datetime
    status: str
    status_label: str

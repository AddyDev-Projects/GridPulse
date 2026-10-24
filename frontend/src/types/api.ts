// Mirrors backend/app/schemas/api.py and backend/app/schemas/agents.py.

export type IncidentStatus =
  | "REPORTED"
  | "TRIAGED"
  | "EVIDENCE_GATHERED"
  | "PLANS_PROPOSED"
  | "VALIDATING"
  | "REVISION_REQUIRED"
  | "VALIDATING_AGAIN"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "EXECUTED"
  | "REJECTED"
  | "ESCALATED"
  | "NO_SAFE_PLAN"
  | "FAILED"
  | "DISMISSED"
  | "RESOLVED";

export type PlanStatus =
  | "DRAFT"
  | "REVISION_REQUIRED"
  | "SUPERSEDED"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXECUTED"
  | "STALE"
  | "ESCALATED"
  | "INVALID";

export type Severity = "blocking" | "warning" | "info";
export type HazardStatus =
  | "CORROBORATED"
  | "OPERATOR_CONFIRMED"
  | "CONTRADICTORY"
  | "STALE"
  | "UNCORROBORATED"
  | "NOT_INDICATED";
export type LatLng = [number, number];

// ---- public ------------------------------------------------------------------------------------
export interface City {
  name: string;
  state: string;
  country: string;
  center: LatLng;
  coverage_bbox: [number, number, number, number];
  graph: { source: string; license: string; osm_base_timestamp: string | null; nodes: number; segments: number; sha256: string; weights: string };
  attribution: string;
  categories: string[];
  indicators: string[];
}

export interface PublicReportInput {
  category: string;
  description: string;
  lat: number;
  lon: number;
  location_description?: string;
  indicators: string[];
  website?: string;
}

export interface PublicReportReceipt {
  reference: string;
  status: string;
  status_label: string;
  submitted_at: string;
  in_coverage: boolean;
  nearest_road: string | null;
}

export interface PublicReportStatus {
  reference: string;
  category: string;
  status: string;
  status_label: string;
  submitted_at: string;
  updated_at: string;
  public_note: string | null;
}

export interface PublicIncident {
  reference: string;
  category: string;
  status: string;
  status_label: string;
  lat: number;
  lon: number;
  advisory: string | null;
  verified: boolean;
  updated_at: string;
  simulated_scenario: boolean;
}

export interface PublicRecentIncident {
  reference: string;
  category: string;
  location: string;
  reported_at: string;
  status: string;
  status_label: string;
}

export interface Closure {
  segment_id: string;
  name: string;
  kind: "closed" | "caution";
  source: "plan_execution" | "scenario";
  reason: string | null;
  created_at: string;
  geometry: LatLng[];
}

export interface RoadSegment {
  id: string;
  name: string;
  highway: string;
  oneway: boolean;
  geometry: LatLng[];
}

// ---- auth --------------------------------------------------------------------------------------
export interface OperatorInfo {
  username: string;
  display_name: string;
  role: "admin" | "operator";
}

export interface LoginResponse {
  operator: OperatorInfo;
  csrf_token: string;
  expires_at: string;
}

// ---- operator ----------------------------------------------------------------------------------
export interface Scenario {
  key: string;
  code: string;
  title: string;
  summary: string;
  expected_outcome: string;
  lat: number;
  lon: number;
  category: string;
  report_text: string;
}

export interface Route {
  id: number;
  plan_id: string;
  purpose: string | null;
  name: string;
  origin_id: string;
  destination_id: string;
  edge_path: string[];
  node_path: string[];
  length_m: number;
  created_at: string;
  geometry: LatLng[];
}

export interface SimulationState {
  city: string;
  state_version: number;
  epoch: number;
  active_scenario: Scenario | null;
  updated_at: string;
  closures: number;
  alerts: { id: string; name: string; kind: string; status: string }[];
  active_routes: Route[];
  incidents_by_status: Record<string, number>;
  pending_approvals: number;
  new_reports: number;
  llm_mode: string;
}

export interface OverlayNode {
  id: string;
  kind: "signal" | "sensor";
  name: string;
  zone: string;
  lat: number;
  lon: number;
  status: string;
  simulated: boolean;
  attributes: Record<string, unknown>;
}

export interface Zone {
  id: string;
  name: string;
  status: string;
  bounds: [number, number, number, number];
  simulated: boolean;
}

export interface Poi {
  id: string;
  kind: "hospital" | "fire_station" | "police";
  name: string;
  lat: number;
  lon: number;
  source: string;
}

export interface Overlay {
  nodes: OverlayNode[];
  zones: Zone[];
  closures: Closure[];
  active_routes: Route[];
  pois: Poi[];
  state_version: number;
  simulated_layers: string[];
}

export interface Incident {
  id: string;
  reference: string | null;
  source: "citizen_portal" | "demo_scenario" | "legacy";
  epoch: number;
  scenario_key: string;
  reporter_alias: string;
  report_text: string;
  location_hint: string | null;
  location_id: string | null;
  lat: number | null;
  lon: number | null;
  segment_id: string | null;
  location_description: string | null;
  observed_at: string | null;
  indicators: string[];
  category: string | null;
  urgency: string | null;
  status: IncidentStatus;
  status_reason: string | null;
  verified_at: string | null;
  verification_note: string | null;
  dismissed_at: string | null;
  dismissal_reason: string | null;
  resolved_at?: string | null;
  resolution_note?: string | null;
  run_started_at: string | null;
  run_finished_at: string | null;
  created_at: string;
  updated_at: string;
  city_id: number | null;
}

export interface IncidentSummary extends Incident {
  current_plan_id: string | null;
  current_plan_version: number | null;
  current_plan_status: PlanStatus | null;
  is_current_epoch: boolean;
  legacy: boolean;
}

export interface SentinelAssessment {
  category: string;
  location_id: string | null;
  location_name: string | null;
  summary: string;
  reported_hazards: string[];
  urgency: "low" | "moderate" | "high" | "critical";
  urgency_indicators: string[];
  reported_facts: string[];
  inferences: string[];
  missing_information: string[];
  assumptions: string[];
  recommended_next_steps: string[];
  confidence: number;
  affected_people: string[];
  potential_hazards: string[];
  responder_recommendations: ResponderRecommendation[];
  medical_need: "indicated" | "uncertain" | "not_indicated";
  medical_need_reason: string | null;
}

export interface ResponderRecommendation {
  responder_type: "fire_rescue" | "ambulance" | "police";
  status: "recommended" | "consider" | "verification_required";
  rationale: string;
  evidence: string[];
  confidence: number;
}

export interface FacilityRecommendation {
  facility_id: string;
  name: string;
  distance_m: number;
  travel_s: number;
  source: string;
  capability_status: string;
  availability_status: string;
  rationale: string;
  kind?: "hospital" | "police" | "fire_station";
  service?: ResponderType | null;
  lat?: number | null;
  lon?: number | null;
  rank?: number | null;
  route_distance_m?: number | null;
}

export type ResponderType = "fire_rescue" | "ambulance" | "police";

export type DepartmentStatus = "required" | "recommended" | "conditional" | "not_justified";

export interface ServiceRequirement {
  service: ResponderType;
  /** "optional" / "not_needed" appear only on records made before the four-status model. */
  status: DepartmentStatus | "optional" | "not_needed";
  basis?: "reported" | "inference" | "rule";
  rationale: string;
  evidence: string[];
  unknowns?: string[];
  expected_action?: string | null;
  uncertainty?: string | null;
}

export interface RouteLeg {
  service: string;
  leg: "to_incident" | "to_hospital";
  purpose: string | null;
  status: "planned" | "unavailable" | "withheld";
  reason?: string | null;
  action_seq?: number | null;
  name?: string | null;
  origin_label?: string | null;
  destination_label?: string | null;
  length_m?: number | null;
  travel_s?: number | null;
}

export interface Department {
  service: ResponderType;
  label: string;
  status: DepartmentStatus;
  basis: "reported" | "inference" | "rule";
  rationale: string;
  evidence: string[];
  unknowns: string[];
  expected_action: string | null;
  routes: RouteLeg[];
}

export interface TrafficRouteAssessment {
  purpose: string;
  service: ResponderType | null;
  name: string;
  status: "clear" | "caution" | "blocked";
  data_source: string;
  travel_time_basis: string;
  length_m: number;
  travel_s: number;
  segments: number;
  closed_segments: string[];
  closed_by_this_plan: string[];
  hazard_segments: string[];
  caution_segments: string[];
  alternative: { length_m: number; travel_s: number; extra_travel_s: number; overlap_pct: number; not_selected_reason?: string } | null;
  alternative_note: string | null;
  selection_reason?: string;
  risks?: string[];
  confidence?: string;
}

export interface TrafficAlternative {
  purpose: string;
  service: ResponderType | null;
  name: string;
  length_m: number;
  travel_s: number;
  extra_travel_s: number;
  overlap_pct: number;
  not_selected_reason?: string;
  geometry?: LatLng[];
}

export interface TrafficAnalysis {
  status?: "completed" | "failed";
  data_source: string;
  live_traffic: string;
  freshness: string;
  plan_version: number | null;
  state_version: number | null;
  route_assessments: TrafficRouteAssessment[];
  traffic_management_recommendations: { action: string; status: string; detail: string; purpose?: string }[];
  adjustments: { type: string; name?: string; detail?: string }[];
  alternative_routes?: TrafficAlternative[];
  evidence_sources?: { source: string; type: string; freshness: string }[];
  affected_junctions?: string[];
  conflicts: string[];
  uncertainty: string[];
}

export interface Observation {
  id: number;
  sensor_id: string;
  sensor_name: string;
  monitors: string;
  measurement: string;
  value: number;
  unit: string;
  threshold: number;
  observed_at: string;
  age_minutes: number;
  freshness: "fresh" | "stale";
  exceeds_threshold: boolean;
  quality: string;
  synthetic: boolean;
}

export interface HazardEvidence {
  target_id: string;
  target_name: string;
  hazard: string;
  status: HazardStatus;
  sensors: string[];
  observation_ids: number[];
  explanation: string;
  segment_ids: string[];
  sensor_status: HazardStatus | null;
}

export interface IncidentGeo {
  lat: number;
  lon: number;
  in_coverage: boolean;
  segment_id: string | null;
  segment_name: string | null;
  snap_distance_m: number | null;
  node_id: string | null;
  zone_id: string | null;
  hazard_radius_m: number;
  hazard_segment_ids: string[];
}

export interface EvidenceAssessment {
  location_id: string | null;
  geo: IncidentGeo | null;
  operator_confirmation: { operator: string; confirmed_at: string; note: string | null } | null;
  location_hazard: HazardEvidence | null;
  infrastructure_hazards: HazardEvidence[];
  observations: Observation[];
  warnings: string[];
  fresh_window_minutes: number;
  assessed_at: string;
}

export interface Proposal {
  id: string;
  agent: string;
  revision_number: number;
  summary: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface IncidentDetail {
  incident: Incident;
  assessment: SentinelAssessment | null;
  assessment_engine: string | null;
  assessment_fallback_reason: string | null;
  evidence: EvidenceAssessment | null;
  proposals: Proposal[];
  is_current_epoch: boolean;
  legacy: boolean;
  verified_by: string | null;
  dismissed_by: string | null;
  resolved_by?: string | null;
  hazard_geometry: LatLng[][];
  services?: ServiceRequirement[];
}

export interface PlanAction {
  seq: number;
  action_type: string;
  label: string;
  target_id: string;
  target_name: string | null;
  params: Record<string, unknown>;
  impact: "low" | "medium" | "high";
  proposed_by: string;
  rationale: string;
  geometry: LatLng[] | null;
  segments_geometry: LatLng[][] | null;
}

export interface Finding {
  id: number;
  rule_id: string;
  severity: Severity;
  agent: string | null;
  action_seq: number | null;
  title: string;
  message: string;
  remediation: string | null;
  evidence: Record<string, unknown>;
  can_proceed: boolean;
}

export interface ValidationRun {
  id: string;
  state_version: number;
  trigger: string;
  passed: boolean;
  blocking_count: number;
  warning_count: number;
  info_count: number;
  created_at: string;
  findings: Finding[];
}

export interface Approval {
  id: string;
  decision: "approved" | "rejected";
  operator: string;
  note: string | null;
  plan_version: number;
  plan_content_hash: string;
  state_version: number;
  status: "active" | "invalidated" | "consumed" | "final";
  invalidated_reason: string | null;
  created_at: string;
}

export interface Execution {
  id: string;
  plan_id: string;
  status: string;
  state_version_before: number;
  state_version_after: number;
  results: {
    seq: number;
    action_type: string;
    target_id: string;
    status: string;
    note?: string;
    changes: { entity: string; id: string; name?: string; before?: string; after: string }[];
  }[];
  executed_by: string;
  executed_at: string;
  notes: string | null;
}

export interface RouteSummary {
  node_path: string[];
  edge_path: string[];
  length_m: number;
  travel_s: number;
  name: string;
}

export interface BriefAction {
  seq: number;
  action_type: string;
  target_id: string;
  params: Record<string, unknown>;
  proposed_by: string;
  impact: string;
}

export interface Revision {
  id: number;
  from_plan_id: string;
  to_plan_id: string;
  revision_number: number;
  strategy: string;
  triggered_by: { rule_id: string; title: string; action_seq: number | null }[];
  alternatives: { strategy: string; agent: string; viable: boolean; selected?: boolean; detail: string; purpose?: string }[];
  diff: {
    added: BriefAction[];
    removed: BriefAction[];
    changed: { before: BriefAction; after: BriefAction }[];
    unchanged: BriefAction[];
    routes_before: Record<string, RouteSummary>;
    routes_after: Record<string, RouteSummary>;
  };
  rationale: string;
  created_at: string;
}

export interface Plan {
  id: string;
  incident_id: string;
  version: number;
  parent_plan_id: string | null;
  status: PlanStatus;
  based_on_state_version: number;
  content_hash: string;
  summary: string;
  rationale: string | null;
  created_at: string;
  actions: PlanAction[];
  latest_validation: ValidationRun | null;
  revision_from_parent: Revision | null;
  approvals: Approval[];
  execution: Execution | null;
  current_state_version: number;
  approvable: boolean;
  approval_blockers: string[];
  departments?: Department[];
}

export interface Transition {
  id: number;
  from_status: string | null;
  to_status: IncidentStatus;
  reason: string | null;
  created_at: string;
}

export interface AgentRun {
  id: string;
  agent: string;
  step: string;
  revision_number: number;
  status: string;
  engine: string;
  model: string | null;
  fallback_used: boolean;
  fallback_reason: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface Workflow {
  incident: Incident;
  transitions: Transition[];
  agent_runs: AgentRun[];
  plans: Plan[];
  revisions: Revision[];
  running: boolean;
  max_revisions: number;
}

export interface AuditEvent {
  id: number;
  incident_id: string | null;
  plan_id: string | null;
  event_type: string;
  actor: string;
  severity: "info" | "warning" | "error" | "success";
  message: string;
  details: Record<string, unknown>;
  created_at: string;
}

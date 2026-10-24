"""Structured agent outputs. Every agent hands the coordinator one of these, never free text."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IncidentCategory = Literal[
    "flood",
    "power_outage",
    "road_obstruction",
    "fire",
    "medical_emergency",
    "accident",
    "rescue",
    "police_assistance",
    "violence_assault",
    "homicide",
    "threat_weapon",
    "theft_robbery_burglary",
    "suspicious_activity",
    "other",
]
Urgency = Literal["low", "moderate", "high", "critical"]
HazardStatus = Literal["CORROBORATED", "OPERATOR_CONFIRMED", "CONTRADICTORY", "STALE", "UNCORROBORATED", "NOT_INDICATED"]
# Statuses that justify high-impact actions: sensor corroboration or an authenticated operator's confirmation.
SUPPORTING_STATUSES = ("CORROBORATED", "OPERATOR_CONFIRMED")
SeverityIndicator = Literal[
    "people_trapped",
    "injuries",
    "multiple_injured",
    "person_unconscious",
    "vehicles_stranded",
    "water_rising",
    "power_lines_down",
    "road_blocked",
    "fire_spreading",
    "structural_damage",
    "weapon_present",
    "suspected_homicide",
    "suspect_present",
]

ResponderType = Literal["fire_rescue", "ambulance", "police"]
RecommendationStatus = Literal["recommended", "consider", "verification_required"]


class ResponderRecommendation(BaseModel):
    """A cautious, evidence-labelled responder recommendation; never a dispatch instruction."""

    model_config = ConfigDict(extra="forbid")

    responder_type: ResponderType
    status: RecommendationStatus = "recommended"
    rationale: str = Field(max_length=400)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)


class FacilityRecommendation(BaseModel):
    """A facility candidate based on the verified OSM location and a simulated route estimate."""

    model_config = ConfigDict(extra="forbid")

    facility_id: str
    name: str
    distance_m: float = Field(ge=0)
    travel_s: float = Field(ge=0)
    source: str = "OpenStreetMap snapshot"
    capability_status: str = "unverified"
    availability_status: str = "unknown"
    rationale: str = Field(max_length=500)


class SentinelAssessment(BaseModel):
    """Sentinel's triage of a citizen report. Produced by an LLM or by the rule-based fallback."""

    model_config = ConfigDict(extra="forbid")

    category: IncidentCategory
    location_id: str | None = Field(default=None, description="Road segment id from map snapping (set by GridPulse)")
    location_name: str | None = None
    summary: str = Field(max_length=400)
    reported_hazards: list[str] = Field(default_factory=list, max_length=10)
    urgency: Urgency
    urgency_indicators: list[str] = Field(default_factory=list, max_length=10)
    reported_facts: list[str] = Field(default_factory=list, max_length=10)
    inferences: list[str] = Field(default_factory=list, max_length=10)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    affected_people: list[str] = Field(default_factory=list, max_length=8)
    potential_hazards: list[str] = Field(default_factory=list, max_length=10)
    responder_recommendations: list[ResponderRecommendation] = Field(default_factory=list, max_length=3)
    medical_need: Literal["indicated", "uncertain", "not_indicated"] = "uncertain"
    medical_need_reason: str | None = Field(default=None, max_length=400)


class Observation(BaseModel):
    id: int
    sensor_id: str
    sensor_name: str
    monitors: str
    measurement: str
    value: float
    unit: str
    threshold: float
    observed_at: datetime
    age_minutes: float
    freshness: Literal["fresh", "stale"]
    exceeds_threshold: bool
    quality: str
    synthetic: bool = True


class HazardEvidence(BaseModel):
    target_id: str
    target_name: str
    hazard: str
    status: HazardStatus
    sensors: list[str]
    observation_ids: list[int]
    explanation: str
    segment_ids: list[str] = Field(default_factory=list, description="Road segments in the modelled hazard area")
    sensor_status: HazardStatus | None = Field(default=None, description="What sensors alone indicated")


class IncidentGeo(BaseModel):
    """Deterministic geographic context of a report, computed from its map coordinates."""

    lat: float
    lon: float
    in_coverage: bool
    segment_id: str | None
    segment_name: str | None
    snap_distance_m: float | None
    node_id: str | None
    zone_id: str | None
    hazard_radius_m: float
    hazard_segment_ids: list[str]


class OperatorConfirmation(BaseModel):
    operator: str
    confirmed_at: datetime
    note: str | None = None


class EvidenceAssessment(BaseModel):
    location_id: str | None
    geo: IncidentGeo | None = None
    operator_confirmation: OperatorConfirmation | None = None
    location_hazard: HazardEvidence | None
    infrastructure_hazards: list[HazardEvidence]
    observations: list[Observation]
    warnings: list[str]
    fresh_window_minutes: int
    assessed_at: datetime

    def hazard_for(self, target_id: str) -> HazardEvidence | None:
        if self.location_hazard and self.location_hazard.target_id == target_id:
            return self.location_hazard
        return next((h for h in self.infrastructure_hazards if h.target_id == target_id), None)

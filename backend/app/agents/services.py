"""Service selection — which responder services an incident justifies (deterministic; no LLM).

Each service (fire & rescue, ambulance, police) is classified as:
  * required   — justified by the citizen's chosen category or a selected indicator. Mobility plans a
                 route for it, which still needs evidence (R6) and operator approval before execution.
  * optional   — plausible but not established (a related category, or only a free-text mention).
                 Shown to the operator with facility candidates; no route or dispatch action is generated.
  * not_needed — nothing in the report supports it.

Facilities on the map (fire stations, police stations, hospitals) never create a requirement by
existing; only the report does. Text-only signals are never promoted to "required" because the
operator should verify them first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.agents import SentinelAssessment, ServiceRequirement

Service = Literal["fire_rescue", "ambulance", "police"]
SERVICES: tuple[Service, ...] = ("fire_rescue", "ambulance", "police")

POLICE_CATEGORIES = {"police_assistance", "violence_assault", "homicide", "threat_weapon",
                     "theft_robbery_burglary", "suspicious_activity"}
FIRE_REQUIRED_CATEGORIES = {"fire": "A fire was reported.",
                            "flood": "Flood rescue and pumping are fire & rescue service tasks.",
                            "rescue": "A rescue need was reported."}
FIRE_INDICATORS = {"fire_spreading": "fire spreading", "people_trapped": "people trapped",
                   "structural_damage": "structural damage"}
MEDICAL_INDICATORS = {"injuries": "injuries reported", "multiple_injured": "multiple injured people",
                      "person_unconscious": "person appears unconscious"}
POLICE_INDICATORS = {"weapon_present": "weapon reported", "suspected_homicide": "possible homicide reported",
                     "suspect_present": "person involved may still be present"}

# Route purpose Mobility plans for each required service (see app.validation.actions.RouteParams).
ROUTE_PURPOSE: dict[Service, str] = {"fire_rescue": "emergency_access", "police": "police_response",
                                     "ambulance": "medical_transfer"}
FACILITY_KIND: dict[Service, str] = {"fire_rescue": "fire_station", "police": "police", "ambulance": "hospital"}


@dataclass
class ServicePlan:
    decisions: list[ServiceRequirement] = field(default_factory=list)

    def status(self, service: Service) -> str:
        return next((d.status for d in self.decisions if d.service == service), "not_needed")

    def required(self) -> list[Service]:
        return [s for s in SERVICES if self.status(s) == "required"]

    def considered(self) -> list[Service]:
        return [s for s in SERVICES if self.status(s) in ("required", "optional")]

    def as_list(self) -> list[dict]:
        return [d.model_dump(mode="json") for d in self.decisions]


def _ev(*items: str | None) -> list[str]:
    return [i for i in items if i]


def _sentinel_suggests(assessment: SentinelAssessment | None, service: Service) -> bool:
    return assessment is not None and any(r.responder_type == service for r in assessment.responder_recommendations)


def select_services(reported_category: str, indicators: list[str], assessment: SentinelAssessment | None) -> ServicePlan:
    """Classify each service from the report. `reported_category` is the citizen's own choice."""
    selected = set(indicators)
    # When the citizen chose "other", Sentinel's reading of the text is the only category signal; it can
    # make a service optional (to verify) but never required.
    text_category = assessment.category if assessment and reported_category == "other" else None
    out: list[ServiceRequirement] = []

    # Fire & rescue
    ev = [FIRE_INDICATORS[i] for i in sorted(selected & FIRE_INDICATORS.keys())]
    if reported_category in FIRE_REQUIRED_CATEGORIES or ev:
        reason = FIRE_REQUIRED_CATEGORIES.get(reported_category) or "Fire & rescue capability is indicated by the report."
        out.append(ServiceRequirement(
            service="fire_rescue", status="required", rationale=reason,
            evidence=_ev(f"reported category: {reported_category}" if reported_category in FIRE_REQUIRED_CATEGORIES else None) + ev))
    elif reported_category in ("road_obstruction", "accident") or "power_lines_down" in selected \
            or text_category in FIRE_REQUIRED_CATEGORIES or _sentinel_suggests(assessment, "fire_rescue"):
        out.append(ServiceRequirement(
            service="fire_rescue", status="optional",
            rationale="Clearance, extrication or a fire hazard may be involved, but the report does not establish it.",
            evidence=_ev(
                f"reported category: {reported_category.replace('_', ' ')}" if reported_category in ("road_obstruction", "accident") else None,
                "power lines down" if "power_lines_down" in selected else None,
                "mentioned in the report text" if text_category in FIRE_REQUIRED_CATEGORIES
                or _sentinel_suggests(assessment, "fire_rescue") else None),
            uncertainty="Verify before requesting fire & rescue."))
    else:
        out.append(ServiceRequirement(service="fire_rescue", status="not_needed",
                                      rationale="No fire, rescue, trapped-person or structural hazard is reported."))

    # Ambulance / medical
    ev = [MEDICAL_INDICATORS[i] for i in sorted(selected & MEDICAL_INDICATORS.keys())]
    if reported_category == "medical_emergency" or ev:
        out.append(ServiceRequirement(
            service="ambulance", status="required",
            rationale="A medical emergency or injured person was reported; a hospital route is planned. No diagnosis is made.",
            evidence=_ev("reported category: medical emergency" if reported_category == "medical_emergency" else None) + ev))
    elif reported_category in ("accident", "rescue", "violence_assault", "homicide") or "people_trapped" in selected \
            or text_category == "medical_emergency" or _sentinel_suggests(assessment, "ambulance"):
        out.append(ServiceRequirement(
            service="ambulance", status="optional",
            rationale="People may need medical care, but no injury is stated. Hospitals are listed for reference only.",
            evidence=_ev(
                f"reported category: {reported_category.replace('_', ' ')}"
                if reported_category in ("accident", "rescue", "violence_assault", "homicide") else None,
                "people trapped" if "people_trapped" in selected else None,
                "mentioned in the report text" if text_category == "medical_emergency"
                or _sentinel_suggests(assessment, "ambulance") else None),
            uncertainty="Confirm whether anyone is hurt before requesting an ambulance."))
    else:
        out.append(ServiceRequirement(service="ambulance", status="not_needed",
                                      rationale="No injury, unconsciousness or other medical need is reported."))

    # Police
    ev = [POLICE_INDICATORS[i] for i in sorted(selected & POLICE_INDICATORS.keys())]
    if reported_category in POLICE_CATEGORIES or ev:
        out.append(ServiceRequirement(
            service="police", status="required",
            rationale="A security or criminal incident was reported. The allegation is unverified; no suspect facts are established.",
            evidence=_ev(f"reported category: {reported_category.replace('_', ' ')}" if reported_category in POLICE_CATEGORIES else None) + ev))
    elif reported_category == "accident" or text_category in POLICE_CATEGORIES or _sentinel_suggests(assessment, "police"):
        out.append(ServiceRequirement(
            service="police", status="optional",
            rationale="Police may be relevant (traffic management or a mentioned security concern), but it is not established.",
            evidence=_ev("reported category: accident" if reported_category == "accident" else None,
                         "mentioned in the report text" if text_category in POLICE_CATEGORIES
                         or _sentinel_suggests(assessment, "police") else None),
            uncertainty="Verify before requesting police."))
    else:
        out.append(ServiceRequirement(service="police", status="not_needed",
                                      rationale="No security, criminal or traffic-control need is reported."))
    return ServicePlan(out)

"""Department selection — which responder services an incident justifies (deterministic; no LLM).

Each department (fire & rescue, ambulance, police) gets exactly one status:
  * required       — justified by what the citizen reported (category or a ticked indicator) or by an
                     explicit operational rule for that category. Mobility plans a road route for it; the
                     route still needs evidence (R6), validation and operator approval.
  * recommended    — useful support under an operational rule (e.g. traffic management around closures),
                     but not a dispatch: no route is planned. The operator may request it.
  * conditional    — would be needed only if something unknown turns out to be true (e.g. injuries at a
                     collision, someone stranded in a flood). Listed with what to verify; no route.
  * not_justified  — nothing in the report or the rules supports it. No route, no recommendation.

Every decision states its basis: "reported" (the citizen's category or indicators), "inference" (a
free-text mention Sentinel picked up, never enough for "required") or "rule" (an operational rule for the
category, named in the rationale). A facility existing nearby never creates a requirement, and nothing
is assigned "just in case": a report that justifies no department gets a verification-only plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.agents import SentinelAssessment, ServiceRequirement

Service = Literal["fire_rescue", "ambulance", "police"]
SERVICES: tuple[Service, ...] = ("fire_rescue", "ambulance", "police")
ASSIGNED = ("required",)  # statuses that get a planned route (a simulated dispatch)
SUPPORT = ("required", "recommended", "conditional")  # statuses shown with facility candidates

POLICE_CATEGORIES = {"police_assistance", "violence_assault", "homicide", "threat_weapon",
                     "theft_robbery_burglary", "suspicious_activity"}
FIRE_INDICATORS = {"fire_spreading": "fire spreading", "people_trapped": "people trapped",
                   "structural_damage": "structural damage"}
FLOOD_RESCUE_INDICATORS = {"people_trapped": "people trapped", "vehicles_stranded": "vehicles stranded"}
MEDICAL_INDICATORS = {"injuries": "injuries reported", "multiple_injured": "multiple injured people",
                      "person_unconscious": "person appears unconscious"}
POLICE_INDICATORS = {"weapon_present": "weapon reported", "suspected_homicide": "possible homicide reported",
                     "suspect_present": "person involved may still be present"}

EXPECTED_ACTION: dict[Service, str] = {
    "fire_rescue": "Respond from the fastest-reachable fire station to the staging point near the incident (simulated).",
    "police": "Respond from the fastest-reachable police station to the incident (simulated).",
    "ambulance": "Attend the patient and transfer to the top-ranked reachable hospital (simulated).",
}

# Route purpose Mobility plans for each required service (see app.validation.actions.RouteParams).
ROUTE_PURPOSE: dict[Service, str] = {"fire_rescue": "emergency_access", "police": "police_response",
                                     "ambulance": "medical_transfer"}
FACILITY_KIND: dict[Service, str] = {"fire_rescue": "fire_station", "police": "police", "ambulance": "hospital"}
# Statuses recorded before this module distinguished recommended/conditional (still in older runs).
LEGACY_STATUS = {"optional": "conditional", "not_needed": "not_justified"}


@dataclass
class ServicePlan:
    decisions: list[ServiceRequirement] = field(default_factory=list)

    def status(self, service: Service) -> str:
        return next((d.status for d in self.decisions if d.service == service), "not_justified")

    def required(self) -> list[Service]:
        return [s for s in SERVICES if self.status(s) in ASSIGNED]

    def considered(self) -> list[Service]:
        return [s for s in SERVICES if self.status(s) in SUPPORT]

    def as_list(self) -> list[dict]:
        return [d.model_dump(mode="json") for d in self.decisions]

    @classmethod
    def from_list(cls, rows: list[dict]) -> ServicePlan:
        return cls([ServiceRequirement.model_validate({**r, "status": LEGACY_STATUS.get(r["status"], r["status"])})
                    for r in rows])


def _picked(selected: set[str], table: dict[str, str]) -> list[str]:
    return [f"indicator: {table[i]}" for i in sorted(selected & table.keys())]


def _sentinel_suggests(assessment: SentinelAssessment | None, service: Service) -> bool:
    return assessment is not None and any(r.responder_type == service for r in assessment.responder_recommendations)


def _decide(service: Service, status: str, basis: str, rationale: str, evidence: list[str] | None = None,
            unknowns: list[str] | None = None) -> ServiceRequirement:
    return ServiceRequirement(service=service, status=status, basis=basis, rationale=rationale,
                              evidence=evidence or [], unknowns=unknowns or [],
                              expected_action=EXPECTED_ACTION[service] if status == "required" else None)


def _fire(cat: str, selected: set[str], text_hint: bool) -> ServiceRequirement:
    cat_ev = [f"reported category: {cat.replace('_', ' ')}"]
    if cat == "fire":
        return _decide("fire_rescue", "required", "reported", "A fire was reported.", cat_ev + _picked(selected, FIRE_INDICATORS))
    if ind := _picked(selected, FIRE_INDICATORS):
        return _decide("fire_rescue", "required", "reported",
                       "Reported entrapment, spreading fire or structural damage needs fire & rescue capability.", ind)
    if cat == "rescue":
        return _decide("fire_rescue", "required", "reported", "A rescue need was reported.", cat_ev)
    if cat == "flood":
        if ind := _picked(selected, FLOOD_RESCUE_INDICATORS):
            return _decide("fire_rescue", "required", "reported",
                           "Flood rescue is a fire & rescue task, and people or vehicles were reported stranded.", cat_ev + ind)
        return _decide("fire_rescue", "conditional", "rule",
                       "Rule: flood rescue and pumping are fire & rescue tasks, but no one was reported stranded or trapped.",
                       cat_ev, ["Whether people or vehicles are stranded", "Water depth and extent"])
    if cat == "road_obstruction":
        return _decide("fire_rescue", "conditional", "rule",
                       "Rule: clearing heavy debris or a fallen tree may need fire & rescue equipment; the size of the obstruction is unknown.",
                       cat_ev, ["Size of the obstruction", "Whether anyone is trapped"])
    if "power_lines_down" in selected:
        return _decide("fire_rescue", "conditional", "reported",
                       "Downed power lines can start fires; no fire was reported.", ["indicator: power lines down"],
                       ["Whether there is a fire or anyone in contact with the lines"])
    if text_hint:
        return _decide("fire_rescue", "conditional", "inference",
                       "Inference: the report text mentions fire or rescue, but no fire category or indicator was chosen.",
                       ["mentioned in the report text"], ["Whether there is a fire or a rescue need"])
    if cat == "accident":
        return _decide("fire_rescue", "not_justified", "reported",
                       "No fire, entrapment, hazardous material or other rescue need was reported at this collision.",
                       cat_ev, ["Whether anyone is trapped"])
    return _decide("fire_rescue", "not_justified", "reported",
                   "No fire, rescue, trapped-person or structural hazard is reported.")


def _ambulance(cat: str, selected: set[str], text_hint: bool) -> ServiceRequirement:
    cat_ev = [f"reported category: {cat.replace('_', ' ')}"]
    if cat == "medical_emergency":
        return _decide("ambulance", "required", "reported",
                       "A medical emergency was reported. No diagnosis is made.", cat_ev + _picked(selected, MEDICAL_INDICATORS))
    if ind := _picked(selected, MEDICAL_INDICATORS):
        return _decide("ambulance", "required", "reported", "An injured or unconscious person was reported.", ind)
    if cat in ("accident", "rescue", "violence_assault", "homicide") or "people_trapped" in selected:
        ev = cat_ev if cat in ("accident", "rescue", "violence_assault", "homicide") else ["indicator: people trapped"]
        return _decide("ambulance", "conditional", "rule",
                       "Rule: people may be hurt in this kind of incident, but no injury was reported. Confirm before requesting an ambulance.",
                       ev, ["Whether anyone is injured, and how many"])
    if text_hint:
        return _decide("ambulance", "conditional", "inference",
                       "Inference: the report text mentions an injury or medical need, but no medical category or indicator was chosen.",
                       ["mentioned in the report text"], ["Whether anyone is injured"])
    return _decide("ambulance", "not_justified", "reported", "No injury, unconsciousness or other medical need is reported.")


def _police(cat: str, selected: set[str], text_hint: bool) -> ServiceRequirement:
    cat_ev = [f"reported category: {cat.replace('_', ' ')}"]
    if cat in POLICE_CATEGORIES:
        return _decide("police", "required", "reported",
                       "A security or criminal incident was reported. The allegation is unverified; no suspect facts are established.",
                       cat_ev + _picked(selected, POLICE_INDICATORS))
    if ind := _picked(selected, POLICE_INDICATORS):
        return _decide("police", "required", "reported", "A security indicator was reported.", ind)
    if cat == "accident":
        return _decide("police", "required", "rule",
                       "Rule: a road collision needs scene safety and traffic control, and a record of the collision.", cat_ev)
    if cat in ("flood", "road_obstruction"):
        return _decide("police", "recommended", "rule",
                       "Rule: traffic management may be needed around simulated closures and diversions. Support only; no route is planned.",
                       cat_ev)
    if text_hint:
        return _decide("police", "conditional", "inference",
                       "Inference: the report text mentions a security concern, but no police category or indicator was chosen.",
                       ["mentioned in the report text"], ["Whether there is a security threat"])
    return _decide("police", "not_justified", "reported", "No security, criminal or traffic-control need is reported.")


def select_services(reported_category: str, indicators: list[str], assessment: SentinelAssessment | None) -> ServicePlan:
    """Decide each department from the report. `reported_category` is the citizen's own choice.

    Sentinel's reading of the text (LLM or rules) can only make a department "conditional"; it can never
    make one required, so a model that misreads the report cannot add a dispatch.
    """
    selected = set(indicators)
    text_cat = assessment.category if assessment and reported_category == "other" else None
    fire_hint = text_cat in ("fire", "flood", "rescue") or _sentinel_suggests(assessment, "fire_rescue")
    med_hint = text_cat == "medical_emergency" or _sentinel_suggests(assessment, "ambulance")
    police_hint = text_cat in POLICE_CATEGORIES or _sentinel_suggests(assessment, "police")
    return ServicePlan([
        _fire(reported_category, selected, fire_hint),
        _ambulance(reported_category, selected, med_hint),
        _police(reported_category, selected, police_hint),
    ])


DEPARTMENT_LABEL: dict[str, str] = {"fire_rescue": "Fire & rescue", "police": "Police", "ambulance": "Ambulance / medical"}
PURPOSE_SERVICE: dict[str, Service] = {v: k for k, v in ROUTE_PURPOSE.items()}


def normalise(departments: list[dict]) -> list[dict]:
    """Older runs used optional / not_needed; read them with today's names."""
    return [{**d, "status": LEGACY_STATUS.get(d.get("status", ""), d.get("status")),
             "basis": d.get("basis") or "reported", "unknowns": d.get("unknowns") or [],
             "expected_action": d.get("expected_action")} for d in departments]


def plan_departments(departments: list[dict], legs: list[dict], actions: list) -> list[dict]:
    """Each department with the route legs THIS plan version actually contains.

    `actions` are the plan's actions (anything with action_type, params and seq). A required department's
    leg is "planned" when the plan has its route, "withheld" when the plan is verification-first, and
    "unavailable" with Mobility's reason when no road route exists. Nothing here invents a route.
    """
    routes = {}
    for a in actions:
        if a.action_type == "designate_response_route":
            svc = a.params.get("service") or PURPOSE_SERVICE.get(a.params.get("purpose", ""))
            routes.setdefault(svc, a)
    verification_first = any(a.action_type == "dispatch_field_verification" for a in actions)
    out = []
    for d in normalise(departments):
        svc = d["service"]
        d_legs: list[dict] = []
        if d["status"] in ASSIGNED:
            if svc == "ambulance":
                d_legs.append(next((leg for leg in legs if leg["service"] == svc and leg["leg"] == "to_incident"), {
                    "service": svc, "leg": "to_incident", "purpose": None, "status": "unavailable",
                    "reason": "No ambulance station is mapped in OpenStreetMap for this area, so the ambulance's "
                              "starting point is unknown and this leg cannot be routed."}))
            leg_name = "to_hospital" if svc == "ambulance" else "to_incident"
            a = routes.get(svc)
            if a is not None:
                p = a.params
                d_legs.append({"service": svc, "leg": leg_name, "purpose": p.get("purpose"), "status": "planned",
                               "action_seq": a.seq, "name": p.get("name"), "origin_label": p.get("origin_label"),
                               "destination_label": p.get("destination_label"), "length_m": p.get("length_m"),
                               "travel_s": p.get("travel_s")})
            else:
                failed = next((leg for leg in legs if leg["service"] == svc and leg["leg"] == leg_name
                               and leg["status"] == "unavailable"), None)
                if failed is not None:
                    d_legs.append(failed)
                elif verification_first:
                    d_legs.append({"service": svc, "leg": leg_name, "purpose": ROUTE_PURPOSE[svc], "status": "withheld",
                                   "reason": "Withheld until the reported hazard is verified (evidence rule R6)."})
                else:
                    d_legs.append({"service": svc, "leg": leg_name, "purpose": ROUTE_PURPOSE[svc], "status": "unavailable",
                                   "reason": "Reason not provided — review required."})
        out.append({**d, "label": DEPARTMENT_LABEL.get(svc, svc), "routes": d_legs})
    return out

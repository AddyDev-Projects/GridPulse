"""Mobility — road closures and response routing on the real Mangaluru road graph.

Algorithm (deterministic; no LLM):
  1. Close every real road segment inside the modelled hazard area (IncidentGeo.hazard_segment_ids).
  2. Staging point: the nearest node on the hazard perimeter (an endpoint of a closed segment that
     lies outside the hazard radius) that responders can actually reach. With no hazard area, the
     node nearest the report.
  3. Emergency access route: Dijkstra by estimated travel time from the nearest reachable OSM fire
     station (police stations if none is reachable) to the staging point, honouring one-way
     restrictions and avoiding closed segments and any coordinator-supplied exclusions.
  4. Medical transfer route (only when injuries or trapped people are reported): staging point to
     the nearest reachable OSM hospital.
Failure to find a path is reported explicitly; no route is ever invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.geo.osm import haversine_m
from app.geo.road_graph import Poi
from app.schemas.agents import EvidenceAssessment, FacilityRecommendation, IncidentGeo, SentinelAssessment
from app.simulation.state import CitySnapshot, RouteResult

MEDICAL_INDICATORS = {"injuries", "people_trapped"}
MAX_CANDIDATES = 12


@dataclass
class ProposedAction:
    action_type: str
    target_id: str
    params: dict[str, Any]
    rationale: str


@dataclass
class PlannedRoute:
    purpose: str
    name: str
    origin: str
    origin_label: str
    destination: str
    destination_label: str
    route: RouteResult

    def action(self, why: str) -> ProposedAction:
        r = self.route
        return ProposedAction("designate_response_route", self.origin, {
            "purpose": self.purpose, "name": self.name, "origin": self.origin, "origin_label": self.origin_label,
            "destination": self.destination, "destination_label": self.destination_label,
            "edge_path": r.edge_path, "node_path": r.node_path, "length_m": r.length_m, "travel_s": r.travel_s,
        }, why)


@dataclass
class MobilityProposal:
    actions: list[ProposedAction]
    routes: list[PlannedRoute]
    failed: list[str]
    staging: str | None
    excluded_edges: list[str]
    excluded_nodes: list[str]
    reasoning: list[str]
    uncertainty: list[str] = field(default_factory=list)
    facility_recommendations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{r.name}: {r.route.length_m / 1000:.2f} km, ~{r.route.travel_s / 60:.1f} min" for r in self.routes]
        parts += [f"no feasible {p.replace('_', ' ')} route" for p in self.failed]
        closed = next((len(a.params["segment_ids"]) for a in self.actions if a.action_type == "close_roads"), 0)
        return (f"Close {closed} road segment(s). " if closed else "") + ("; ".join(parts) or "No routing required.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.__dict__ for a in self.actions],
            "routes": [{"purpose": r.purpose, "name": r.name, "origin": r.origin, "origin_label": r.origin_label,
                        "destination": r.destination, "destination_label": r.destination_label, **r.route.as_dict()}
                       for r in self.routes],
            "failed": self.failed, "staging": self.staging, "excluded_edges": self.excluded_edges,
            "excluded_nodes": self.excluded_nodes, "reasoning": self.reasoning, "uncertainty": self.uncertainty,
            "facility_recommendations": self.facility_recommendations,
        }


def _pois(snapshot: CitySnapshot, kind: str, lat: float, lon: float) -> list[Poi]:
    return sorted((p for p in snapshot.graph.pois if p.kind == kind), key=lambda p: haversine_m(lat, lon, p.lat, p.lon))


def staging_candidates(snapshot: CitySnapshot, geo: IncidentGeo) -> list[str]:
    g = snapshot.graph
    if not geo.hazard_segment_ids:
        return [geo.node_id] if geo.node_id else []
    perimeter = set()
    for sid in geo.hazard_segment_ids:
        seg = g.segments[sid]
        for n in (seg.u, seg.v):
            if haversine_m(geo.lat, geo.lon, *g.nodes[n]) > geo.hazard_radius_m:
                perimeter.add(n)
    return sorted(perimeter, key=lambda n: (haversine_m(geo.lat, geo.lon, *g.nodes[n]), n))[:MAX_CANDIDATES]


def access_route(snapshot: CitySnapshot, geo: IncidentGeo, exclude_edges: set[str], exclude_nodes: set[str]
                 ) -> tuple[PlannedRoute | None, str]:
    g = snapshot.graph
    bases = _pois(snapshot, "fire_station", geo.lat, geo.lon) + _pois(snapshot, "police", geo.lat, geo.lon)
    for staging in staging_candidates(snapshot, geo):
        for base in bases[:4]:
            near = g.nearest_node(base.lat, base.lon, 600)
            if near is None:
                continue
            r = snapshot.shortest_route(near[0], staging, exclude_edges, exclude_nodes)
            if r is not None:
                return PlannedRoute("emergency_access", f"Emergency access from {base.label}", near[0], base.label,
                                    staging, f"Staging point near {g.node_label(staging)}", r), staging
    return None, ""


def medical_route(snapshot: CitySnapshot, geo: IncidentGeo, staging: str, exclude_edges: set[str],
                  exclude_nodes: set[str]) -> PlannedRoute | None:
    g = snapshot.graph
    for h in _pois(snapshot, "hospital", geo.lat, geo.lon)[:5]:
        near = g.nearest_node(h.lat, h.lon, 600)
        if near is None:
            continue
        r = snapshot.shortest_route(staging, near[0], exclude_edges, exclude_nodes)
        if r is not None:
            return PlannedRoute("medical_transfer", f"Medical transfer to {h.label}", staging,
                                f"Staging point near {g.node_label(staging)}", near[0], h.label, r)
    return None


def hospital_recommendations(
    snapshot: CitySnapshot,
    geo: IncidentGeo,
    staging: str,
    exclude_edges: set[str],
    exclude_nodes: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rank reachable OSM hospital locations without claiming capability or availability."""
    recommendations: list[dict[str, Any]] = []
    warnings: list[str] = []
    g = snapshot.graph
    for h in _pois(snapshot, "hospital", geo.lat, geo.lon)[:5]:
        near = g.nearest_node(h.lat, h.lon, 600)
        if near is None:
            warnings.append(f"{h.label} is not close enough to the mapped road graph for a route estimate.")
            continue
        route = snapshot.shortest_route(staging, near[0], exclude_edges, exclude_nodes)
        if route is None:
            warnings.append(f"No route estimate reaches {h.label} from the staging point under current road constraints.")
            continue
        recommendation = FacilityRecommendation(
            facility_id=h.id,
            name=h.label,
            distance_m=round(haversine_m(geo.lat, geo.lon, h.lat, h.lon), 1),
            travel_s=route.travel_s,
            source="OpenStreetMap snapshot",
            capability_status="unverified",
            availability_status="unknown",
            rationale=("Reachable by the current simulated road graph with an estimated free-flow travel time. "
                       "Hospital capability, emergency-department status, beds, and acceptance are unknown."),
        )
        recommendations.append(recommendation.model_dump(mode="json"))
    return recommendations, warnings


def propose(
    snapshot: CitySnapshot,
    assessment: SentinelAssessment,
    evidence: EvidenceAssessment,
    geo: IncidentGeo,
    indicators: list[str],
    *,
    exclude_nodes: set[str] | None = None,
    exclude_edges: set[str] | None = None,
) -> MobilityProposal:
    exclude_nodes = set(exclude_nodes or ())
    exclude_edges = set(exclude_edges or ())
    reasoning: list[str] = []
    uncertainty: list[str] = []
    actions: list[ProposedAction] = []

    if not geo.in_coverage or geo.segment_id is None:
        return MobilityProposal([], [], [], None, [], [], ["Report is not on the mapped Mangaluru road network; "
                                                           "no road action or route can be computed."])

    to_close = [s for s in geo.hazard_segment_ids if snapshot.road_status.get(s) != "closed"]
    if to_close:
        actions.append(ProposedAction(
            "close_roads", geo.segment_id,
            {"segment_ids": to_close, "radius_m": geo.hazard_radius_m, "reason": f"Reported {assessment.category}"},
            f"Close {len(to_close)} real road segment(s) within the modelled {geo.hazard_radius_m:.0f} m "
            f"{assessment.category} radius around the reported location ({geo.segment_name})."))
        reasoning.append(f"Hazard area: {len(geo.hazard_segment_ids)} segment(s) within {geo.hazard_radius_m:.0f} m "
                         "(modelling assumption per category, not a measured extent).")
    exclude_edges |= set(geo.hazard_segment_ids)
    if evidence.location_hazard and evidence.location_hazard.status not in ("CORROBORATED", "OPERATOR_CONFIRMED"):
        uncertainty.append(f"Hazard evidence is {evidence.location_hazard.status}; closures are based on the citizen report.")

    planned: list[PlannedRoute] = []
    failed: list[str] = []
    facilities: list[dict[str, Any]] = []
    access, staging = access_route(snapshot, geo, exclude_edges, exclude_nodes)
    if access is None:
        failed.append("emergency_access")
        reasoning.append("No responder base (fire or police station in OSM) can reach any staging point on the "
                         "hazard perimeter with the current closures and exclusions.")
    else:
        planned.append(access)
        reasoning.append(f"{access.name}: {len(access.route.edge_path)} segments, {access.route.length_m:.0f} m, "
                         f"~{access.route.travel_s / 60:.1f} min estimated free-flow (not live traffic).")
        medical_needed = bool(set(indicators) & MEDICAL_INDICATORS) or assessment.urgency == "critical" \
            or assessment.medical_need in ("indicated", "uncertain")
        if medical_needed:
            facilities, facility_warnings = hospital_recommendations(snapshot, geo, staging, exclude_edges, exclude_nodes)
            uncertainty.extend(facility_warnings)
            if facilities:
                reasoning.append(f"{len(facilities)} reachable hospital candidate(s) ranked by estimated route; "
                                 "capability and availability are unverified.")
            med = medical_route(snapshot, geo, staging, exclude_edges, exclude_nodes)
            if med is None:
                failed.append("medical_transfer")
                reasoning.append("No hospital among the five nearest is reachable from the staging point.")
            else:
                planned.append(med)
                reasoning.append(f"{med.name}: {med.route.length_m:.0f} m, ~{med.route.travel_s / 60:.1f} min.")
    if exclude_nodes:
        reasoning.append(f"Avoided junctions at the coordinator's request: {', '.join(sorted(exclude_nodes))}.")
    signals = sorted({d["signal"] for r in planned for d in r.route.signal_dependencies})
    if signals:
        reasoning.append(f"Routes pass junctions with traffic signals {', '.join(signals)} (locations from OSM).")
    for r in planned:
        actions.append(r.action("Fastest path by estimated travel time on the OSM road graph, one-way aware."))
    return MobilityProposal(actions, planned, failed, staging or None, sorted(exclude_edges), sorted(exclude_nodes),
                            reasoning, uncertainty, facilities)

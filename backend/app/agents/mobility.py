"""Mobility — road closures and response routing on the real Mangaluru road graph.

Algorithm (deterministic; no LLM):
  1. Close every real road segment inside the modelled hazard area (IncidentGeo.hazard_segment_ids).
  2. Staging point: the nearest node on the hazard perimeter (an endpoint of a closed segment that
     lies outside the hazard radius) that responders can actually reach. With no hazard area, the
     node nearest the report.
  3. One route per *required* service (app.agents.services), Dijkstra by estimated travel time,
     honouring one-way restrictions and avoiding the hazard area, closed segments and any
     coordinator-supplied exclusions:
       fire & rescue  nearest reachable OSM fire station  → staging point  (emergency_access)
       police         nearest reachable OSM police station → staging point (police_response)
       ambulance      staging point → nearest reachable OSM hospital       (medical_transfer)
     Optional services get ranked facility candidates only; no route or dispatch action.
Failure to find a path is reported explicitly; no route is ever invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.geo.osm import haversine_m
from app.geo.road_graph import Poi
from app.agents.services import FACILITY_KIND, ROUTE_PURPOSE, Service, ServicePlan, select_services
from app.schemas.agents import EvidenceAssessment, FacilityRecommendation, IncidentGeo, SentinelAssessment
from app.simulation.state import CitySnapshot, RouteResult

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
    service: Service | None = None

    def action(self, why: str) -> ProposedAction:
        r = self.route
        return ProposedAction("designate_response_route", self.origin, {
            "purpose": self.purpose, "service": self.service, "name": self.name, "origin": self.origin,
            "origin_label": self.origin_label,
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
    service_requirements: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{r.name}: {r.route.length_m / 1000:.2f} km, ~{r.route.travel_s / 60:.1f} min" for r in self.routes]
        parts += [f"no feasible {p.replace('_', ' ')} route" for p in self.failed]
        closed = next((len(a.params["segment_ids"]) for a in self.actions if a.action_type == "close_roads"), 0)
        return (f"Close {closed} road segment(s). " if closed else "") + ("; ".join(parts) or "No responder route required.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.__dict__ for a in self.actions],
            "routes": [{"purpose": r.purpose, "service": r.service, "name": r.name, "origin": r.origin, "origin_label": r.origin_label,
                        "destination": r.destination, "destination_label": r.destination_label, **r.route.as_dict()}
                       for r in self.routes],
            "failed": self.failed, "staging": self.staging, "excluded_edges": self.excluded_edges,
            "excluded_nodes": self.excluded_nodes, "reasoning": self.reasoning, "uncertainty": self.uncertainty,
            "facility_recommendations": self.facility_recommendations,
            "service_requirements": self.service_requirements,
        }


def _pois(snapshot: CitySnapshot, kind: str, lat: float, lon: float) -> list[Poi]:
    return sorted((p for p in snapshot.graph.pois if p.kind == kind), key=lambda p: haversine_m(lat, lon, p.lat, p.lon))


STAGING_SEARCH_DEG = 0.003  # ~330 m: nearby junctions considered when the perimeter alone is not enough


def staging_candidates(snapshot: CitySnapshot, geo: IncidentGeo) -> list[str]:
    """Perimeter nodes of the hazard area first (nearest first), then nearby junctions outside it.

    The extra junctions matter when the perimeter is a dead end: a node can be reachable yet have no way
    onward except through the hazard area.
    """
    g = snapshot.graph
    hazard = set(geo.hazard_segment_ids)
    if not hazard:
        base = [geo.node_id] if geo.node_id else []
    else:
        perimeter = set()
        for sid in hazard:
            seg = g.segments[sid]
            for n in (seg.u, seg.v):
                if haversine_m(geo.lat, geo.lon, *g.nodes[n]) > geo.hazard_radius_m:
                    perimeter.add(n)
        base = sorted(perimeter, key=lambda n: (haversine_m(geo.lat, geo.lon, *g.nodes[n]), n))
    d = STAGING_SEARCH_DEG
    nearby = {n for sid in g.segments_in_bbox(geo.lat - d, geo.lon - d, geo.lat + d, geo.lon + d, limit=600)
              if sid not in hazard for n in (g.segments[sid].u, g.segments[sid].v)}
    extra = sorted((n for n in nearby if n not in base and haversine_m(geo.lat, geo.lon, *g.nodes[n]) > geo.hazard_radius_m),
                   key=lambda n: (haversine_m(geo.lat, geo.lon, *g.nodes[n]), n))
    return (base + extra)[:MAX_CANDIDATES]


ROUTE_NAMES = {"emergency_access": "Fire & rescue access from {base}", "police_response": "Police response from {base}"}


def _fastest(snapshot: CitySnapshot, geo: IncidentGeo, kind: str, staging: str, exclude_edges: set[str],
             exclude_nodes: set[str], to_facility: bool) -> tuple[Poi, str, RouteResult] | None:
    """The reachable facility of one kind with the shortest estimated travel time (eight nearest considered)."""
    best: tuple[Poi, str, RouteResult] | None = None
    for f in _pois(snapshot, kind, geo.lat, geo.lon)[:8]:
        near = snapshot.graph.nearest_node(f.lat, f.lon, 600)
        if near is None:
            continue
        r = (snapshot.shortest_route(staging, near[0], exclude_edges, exclude_nodes) if to_facility
             else snapshot.shortest_route(near[0], staging, exclude_edges, exclude_nodes))
        if r is not None and (best is None or (r.travel_s, f.id) < (best[2].travel_s, best[0].id)):
            best = (f, near[0], r)
    return best


def _base_route(snapshot: CitySnapshot, geo: IncidentGeo, service: Service, staging: str, exclude_edges: set[str],
                exclude_nodes: set[str]) -> PlannedRoute | None:
    """Route from the fastest-reachable facility of the service's own kind to the staging point.

    Fire & rescue starts at fire stations, police at police stations. There is no cross-service
    fallback: a police station is never used as a fire base, nor the reverse.
    """
    purpose = ROUTE_PURPOSE[service]
    best = _fastest(snapshot, geo, FACILITY_KIND[service], staging, exclude_edges, exclude_nodes, to_facility=False)
    if best is None:
        return None
    base, node, r = best
    return PlannedRoute(purpose, ROUTE_NAMES[purpose].format(base=base.label), node, base.label,
                        staging, f"Staging point near {snapshot.graph.node_label(staging)}", r, service)


def medical_route(snapshot: CitySnapshot, geo: IncidentGeo, staging: str, exclude_edges: set[str],
                  exclude_nodes: set[str]) -> PlannedRoute | None:
    best = _fastest(snapshot, geo, "hospital", staging, exclude_edges, exclude_nodes, to_facility=True)
    if best is None:
        return None
    h, node, r = best
    return PlannedRoute("medical_transfer", f"Medical transfer to {h.label}", staging,
                        f"Staging point near {snapshot.graph.node_label(staging)}", node, h.label, r, "ambulance")


def service_route(snapshot: CitySnapshot, geo: IncidentGeo, service: Service, staging: str, exclude_edges: set[str],
                  exclude_nodes: set[str]) -> PlannedRoute | None:
    if service == "ambulance":
        return medical_route(snapshot, geo, staging, exclude_edges, exclude_nodes)
    return _base_route(snapshot, geo, service, staging, exclude_edges, exclude_nodes)


def choose_staging(snapshot: CitySnapshot, geo: IncidentGeo, required: list[Service], exclude_edges: set[str],
                   exclude_nodes: set[str]) -> tuple[str, dict[Service, PlannedRoute]]:
    """The nearest staging candidate that every required service can use.

    A perimeter node can be reachable from a station yet have no way out except through the hazard area,
    which would leave the ambulance stranded; so candidates are checked for all required routes. If no
    candidate serves them all, the one serving the most (nearest first) is used and the rest are reported
    as failed rather than invented.
    """
    candidates = staging_candidates(snapshot, geo)
    if not candidates:
        return "", {}
    if not required:
        return candidates[0], {}
    best: tuple[str, dict[Service, PlannedRoute]] = ("", {})
    for staging in candidates:
        routes: dict[Service, PlannedRoute] = {}
        for service in required:
            r = service_route(snapshot, geo, service, staging, exclude_edges, exclude_nodes)
            if r is not None:
                routes[service] = r
        if len(routes) == len(required):
            return staging, routes
        if len(routes) > len(best[1]):
            best = (staging, routes)
    return best


def facility_recommendations(
    snapshot: CitySnapshot,
    geo: IncidentGeo,
    service: Service,
    staging: str,
    exclude_edges: set[str],
    exclude_nodes: set[str],
    limit: int = 3,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Rank reachable OSM facilities of one kind by road-network travel time.

    The eight nearest by straight line are routed on the graph (honouring closures and exclusions) and
    ranked by estimated free-flow travel time, so a facility across a river or behind a closure ranks
    by how long it actually takes to reach. Capability, staffing and availability are never claimed.
    """
    kind = FACILITY_KIND[service]
    found: list[tuple[float, Poi, RouteResult]] = []
    warnings: list[str] = []
    g = snapshot.graph
    for f in _pois(snapshot, kind, geo.lat, geo.lon)[:8]:
        near = g.nearest_node(f.lat, f.lon, 600)
        if near is None:
            warnings.append(f"{f.label} is not close enough to the mapped road graph for a route estimate.")
            continue
        # Hospitals are destinations (transfer); stations are origins (response to the incident).
        route = (snapshot.shortest_route(staging, near[0], exclude_edges, exclude_nodes) if kind == "hospital"
                 else snapshot.shortest_route(near[0], staging, exclude_edges, exclude_nodes))
        if route is None:
            warnings.append(f"No route estimate connects {f.label} and the staging point under current road constraints.")
            continue
        found.append((route.travel_s, f, route))
    found.sort(key=lambda x: (x[0], x[1].id))
    what = {"hospital": "Hospital capability, emergency-department status, beds and acceptance are unknown.",
            "police": "Station staffing, unit availability and vehicle positions are unknown.",
            "fire_station": "Appliance availability and crew status are unknown."}[kind]
    recommendations = [FacilityRecommendation(
        facility_id=f.id, name=f.label, kind=kind, service=service, lat=f.lat, lon=f.lon, rank=i,
        distance_m=round(haversine_m(geo.lat, geo.lon, f.lat, f.lon), 1), route_distance_m=round(route.length_m, 1),
        travel_s=route.travel_s, source="OpenStreetMap snapshot", capability_status="unverified",
        availability_status="unknown",
        rationale=f"Ranked {i} by estimated free-flow travel time on the road graph. {what}",
    ).model_dump(mode="json") for i, (_, f, route) in enumerate(found[:limit], start=1)]
    return recommendations, warnings


def hospital_recommendations(snapshot: CitySnapshot, geo: IncidentGeo, staging: str, exclude_edges: set[str],
                             exclude_nodes: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    return facility_recommendations(snapshot, geo, "ambulance", staging, exclude_edges, exclude_nodes, limit=5)


def propose(
    snapshot: CitySnapshot,
    assessment: SentinelAssessment,
    evidence: EvidenceAssessment,
    geo: IncidentGeo,
    indicators: list[str],
    *,
    services: ServicePlan | None = None,
    exclude_nodes: set[str] | None = None,
    exclude_edges: set[str] | None = None,
) -> MobilityProposal:
    services = services or select_services(assessment.category, indicators, assessment)
    exclude_nodes = set(exclude_nodes or ())
    exclude_edges = set(exclude_edges or ())
    reasoning: list[str] = []
    uncertainty: list[str] = []
    actions: list[ProposedAction] = []

    if not geo.in_coverage or geo.segment_id is None:
        return MobilityProposal([], [], [], None, [], [], ["Report is not on the mapped Mangaluru road network; "
                                                           "no road action or route can be computed."],
                                service_requirements=services.as_list())

    to_close = [s for s in geo.hazard_segment_ids if snapshot.road_status.get(s) != "closed"]
    if to_close:
        actions.append(ProposedAction(
            "close_roads", geo.segment_id,
            {"segment_ids": to_close, "radius_m": geo.hazard_radius_m, "reason": f"Reported {assessment.category}"},
            f"Close {len(to_close)} real road segment(s) within the modelled {geo.hazard_radius_m:.0f} m "
            f"{assessment.category} radius around the reported location ({geo.segment_name})."))
        reasoning.append(f"Hazard area: {len(geo.hazard_segment_ids)} segment(s) within {geo.hazard_radius_m:.0f} m "
                         "(modelling assumption per category, not a measured extent).")
    # Routes never use the hazard area or the segments this plan closes.
    exclude_edges |= set(geo.hazard_segment_ids)
    if evidence.location_hazard and evidence.location_hazard.status not in ("CORROBORATED", "OPERATOR_CONFIRMED"):
        uncertainty.append(f"Hazard evidence is {evidence.location_hazard.status}; actions are based on the citizen report.")

    required = services.required()
    reasoning.append("Services: " + "; ".join(f"{d.service.replace('_', ' ')} {d.status.replace('_', ' ')}"
                                              for d in services.decisions) + ".")
    planned: list[PlannedRoute] = []
    failed: list[str] = []
    staging, found = choose_staging(snapshot, geo, required, exclude_edges, exclude_nodes)
    for service in required:
        r = found.get(service)
        if r is None:
            failed.append(ROUTE_PURPOSE[service])
            reasoning.append(f"No {FACILITY_KIND[service].replace('_', ' ')} in OSM is connected to a staging point near the "
                             f"incident for the {service.replace('_', ' ')} route with the current closures and exclusions.")
            continue
        planned.append(r)
        reasoning.append(f"{r.name}: {len(r.route.edge_path)} segments, {r.route.length_m:.0f} m, "
                         f"~{r.route.travel_s / 60:.1f} min estimated free-flow (not live traffic).")

    facilities: list[dict[str, Any]] = []
    if staging:
        for service in services.considered():
            recs, warns = facility_recommendations(snapshot, geo, service, staging, exclude_edges, exclude_nodes,
                                                   limit=5 if service == "ambulance" else 3)
            facilities.extend(recs)
            uncertainty.extend(warns)
        if facilities:
            reasoning.append(f"{len(facilities)} reachable facility candidate(s) ranked by estimated route; "
                             "capability and availability are unverified.")
    if exclude_nodes:
        reasoning.append(f"Avoided junctions at the coordinator's request: {', '.join(sorted(exclude_nodes))}.")
    signals = sorted({d["signal"] for r in planned for d in r.route.signal_dependencies})
    if signals:
        reasoning.append(f"Routes pass junctions with traffic signals {', '.join(signals)} (locations from OSM).")
    for r in planned:
        actions.append(r.action("Fastest path by estimated travel time on the OSM road graph, one-way aware."))
    return MobilityProposal(actions, planned, failed, staging or None, sorted(exclude_edges), sorted(exclude_nodes),
                            reasoning, uncertainty, facilities, services.as_list())

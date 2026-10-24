"""AnalyseTraffic — deterministic traffic and disruption analysis.

GridPulse currently has no live traffic feed. This agent therefore never labels a road as
live-congested: it reports the live-data gap explicitly, uses the OSM graph snapshot and the
simulated closure/caution state for bounded estimates, and leaves executable actions to the
existing allowlist and validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.mobility import MobilityProposal
from app.schemas.agents import EvidenceAssessment, IncidentGeo, SentinelAssessment
from app.simulation.state import CitySnapshot


@dataclass
class TrafficProposal:
    data_source: str
    live_traffic: str
    freshness: str
    affected_segments: list[str]
    route_assessments: list[dict[str, Any]]
    alternative_routes: list[dict[str, Any]]
    conflicts: list[str]
    traffic_management_recommendations: list[dict[str, Any]]
    assumptions: list[str]
    uncertainty: list[str] = field(default_factory=list)
    excluded_edges: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        affected = f"{len(self.affected_segments)} affected segment(s)" if self.affected_segments else "no mapped affected segments"
        routes = f"{len(self.route_assessments)} route assessment(s)"
        return f"Live traffic unavailable; {affected} analysed from the OSM snapshot and simulated state; {routes}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "data_source": self.data_source,
            "live_traffic": self.live_traffic,
            "freshness": self.freshness,
            "affected_segments": self.affected_segments,
            "route_assessments": self.route_assessments,
            "alternative_routes": self.alternative_routes,
            "conflicts": self.conflicts,
            "traffic_management_recommendations": self.traffic_management_recommendations,
            "assumptions": self.assumptions,
            "uncertainty": self.uncertainty,
            "excluded_edges": self.excluded_edges,
        }


def analyse(
    snapshot: CitySnapshot,
    assessment: SentinelAssessment,
    evidence: EvidenceAssessment,
    geo: IncidentGeo,
    mobility: MobilityProposal,
) -> TrafficProposal:
    """Analyse disruption around the incident and reconcile it with Mobility's routes."""
    hazard_segments = set(geo.hazard_segment_ids)
    closed = snapshot.closed_segments()
    caution = {sid for sid, status in snapshot.road_status.items() if status == "caution"}
    affected = sorted(hazard_segments | closed | caution)
    route_assessments: list[dict[str, Any]] = []
    alternative_routes: list[dict[str, Any]] = []
    conflicts: list[str] = []
    excluded_edges = sorted(hazard_segments | closed)

    for planned in mobility.routes:
        route_edges = set(planned.route.edge_path)
        route_caution = sorted(route_edges & caution)
        route_closed = sorted(route_edges & closed)
        route_assessments.append({
            "purpose": planned.purpose,
            "name": planned.name,
            "status": "estimated_free_flow",
            "data_source": "OSM road graph snapshot",
            "length_m": planned.route.length_m,
            "travel_s": planned.route.travel_s,
            "segments": len(planned.route.edge_path),
            "caution_segments": route_caution,
            "closed_segments": route_closed,
            "traffic_delay_s": None,
        })
        if route_closed:
            conflicts.append(f"{planned.name} intersects a closed segment: {', '.join(route_closed[:4])}.")
        if route_caution:
            conflicts.append(f"{planned.name} uses caution segment(s): {', '.join(route_caution[:4])}.")
        alternative = snapshot.shortest_route(
            planned.origin,
            planned.destination,
            exclude_edges=route_edges | hazard_segments | closed,
        )
        if alternative is not None and alternative.edge_path != planned.route.edge_path:
            alternative_routes.append({
                "purpose": planned.purpose,
                "name": f"Alternative to {planned.name}",
                "status": "estimated_free_flow",
                "data_source": "OSM road graph snapshot",
                "length_m": alternative.length_m,
                "travel_s": alternative.travel_s,
                "edge_path": alternative.edge_path,
                "node_path": alternative.node_path,
            })

    shared_edges: set[str] = set()
    for i, left in enumerate(mobility.routes):
        for right in mobility.routes[i + 1:]:
            shared = set(left.route.edge_path) & set(right.route.edge_path)
            if shared:
                shared_edges |= shared
                conflicts.append(
                    f"{left.purpose} and {right.purpose} share {len(shared)} road segment(s); coordinate priority rather than adding contradictory closures."
                )

    recommendations: list[dict[str, Any]] = []
    closure = next((a for a in mobility.actions if a.action_type == "close_roads"), None)
    if closure:
        recommendations.append({
            "action": "simulated_road_closure",
            "status": "reconciled_with_mobility",
            "detail": f"Review Mobility's proposed closure of {len(closure.params.get('segment_ids', []))} hazard-area segment(s); do not add a separate contradictory closure.",
            "evidence": "Modelled hazard area and Mobility's graph-based proposal",
        })
    for planned in mobility.routes:
        recommendations.append({
            "action": "priority_corridor",
            "status": "simulated_recommendation",
            "purpose": planned.purpose,
            "detail": f"Keep the validated {planned.name} corridor clear in the simulation; no live signal change is requested.",
            "evidence": "Route geometry and estimated free-flow travel time from the OSM graph",
        })
    if closed or caution:
        recommendations.append({
            "action": "diversion_awareness",
            "status": "simulated_recommendation",
            "detail": "Warn of existing simulated road constraints and use the validated route alternatives where available.",
            "evidence": "Persisted simulated closure/caution state",
        })
    if shared_edges:
        recommendations.append({
            "action": "route_coordination",
            "status": "simulated_recommendation",
            "detail": f"Coordinate responder priority on {len(shared_edges)} shared segment(s); do not add an independent closure.",
            "evidence": "Overlap between Mobility route geometries",
        })

    uncertainty = [
        "No live traffic feed is configured; current congestion, queue length, signal timing, and incident-related delay are unavailable.",
        "Travel times are free-flow estimates from the versioned OSM road graph, not observations.",
    ]
    if evidence.location_hazard and evidence.location_hazard.status not in ("CORROBORATED", "OPERATOR_CONFIRMED"):
        uncertainty.append(f"The location hazard is {evidence.location_hazard.status}; traffic disruption around it is an estimate from the report and map model.")
    if not geo.in_coverage:
        uncertainty.append("The report is outside the mapped coverage area, so traffic impacts cannot be modelled reliably.")

    return TrafficProposal(
        data_source="estimated",
        live_traffic="unavailable",
        freshness=snapshot.graph.meta.get("osm_base_timestamp") or "versioned road-graph snapshot",
        affected_segments=affected,
        route_assessments=route_assessments,
        alternative_routes=alternative_routes,
        conflicts=conflicts,
        traffic_management_recommendations=recommendations,
        assumptions=[
            "The incident pin is the location to analyse.",
            f"The reported {assessment.category.replace('_', ' ')} is not treated as independently observed unless evidence supports it.",
            "Traffic-management recommendations remain simulated guidance and are not real signal or road-control commands.",
        ],
        uncertainty=uncertainty,
        excluded_edges=excluded_edges,
    )

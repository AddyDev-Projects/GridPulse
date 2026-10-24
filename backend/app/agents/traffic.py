"""AnalyseTraffic — deterministic traffic and disruption analysis of one plan version.

GridPulse has no live traffic feed. This agent never labels a road as live-congested: it reports the
live-data gap explicitly and works from the versioned OSM road graph plus the simulated closure and
caution state. Travel times are free-flow estimates.

Two entry points, both pure:
  * adjust_plan  — changes the draft plan before validation where traffic state justifies it:
                   a route that uses caution segments is replaced by a clear alternative when the
                   detour is small, and a plan that closes roads gets a diversion advisory.
  * analyse_plan — assesses every route of a plan version against the state that plan would
                   produce (its own closures and cautions applied), with meaningfully different
                   alternatives. The output is stamped with the plan and city-state version it
                   describes, so later validation can tell when it no longer applies.
Power dependencies (dark signals) stay with the validator and Grid/Utility; this agent does not
pre-empt them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.agents import EvidenceAssessment, IncidentGeo, SentinelAssessment
from app.simulation.state import ActionSpec, CitySnapshot, apply_actions
from app.validation.actions import ROUTE_ACTIONS

# A route through caution segments is replaced only if the clear alternative is at most this much slower.
CAUTION_DETOUR_MAX_RATIO = 1.25
CAUTION_DETOUR_MAX_EXTRA_S = 240.0
# An alternative sharing more than this fraction of the original's segments is not reported as an alternative.
MAX_ALTERNATIVE_OVERLAP = 0.8
SENSITIVE_CATEGORIES = {"police_assistance", "violence_assault", "homicide", "threat_weapon",
                        "theft_robbery_burglary", "suspicious_activity", "medical_emergency"}
DATA_SOURCE = "OSM road graph snapshot + simulated closure/caution state"
LIVE_TRAFFIC = "unavailable"


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
    plan_version: int | None = None
    state_version: int | None = None
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)
    affected_junctions: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        affected = f"{len(self.affected_segments)} affected segment(s)" if self.affected_segments else "no mapped affected segments"
        blocked = sum(1 for r in self.route_assessments if r["status"] == "blocked")
        routes = f"{len(self.route_assessments)} route assessment(s)" + (f", {blocked} blocked" if blocked else "")
        changed = f"; {len(self.adjustments)} plan change(s)" if self.adjustments else ""
        return (f"Live traffic unavailable; {affected} analysed from the OSM snapshot and simulated state; "
                f"{routes}{changed}.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "data_source": self.data_source,
            "live_traffic": self.live_traffic,
            "freshness": self.freshness,
            "plan_version": self.plan_version,
            "state_version": self.state_version,
            "affected_segments": self.affected_segments,
            "route_assessments": self.route_assessments,
            "alternative_routes": self.alternative_routes,
            "conflicts": self.conflicts,
            "traffic_management_recommendations": self.traffic_management_recommendations,
            "adjustments": self.adjustments,
            "evidence_sources": self.evidence_sources,
            "affected_junctions": self.affected_junctions,
            "assumptions": self.assumptions,
            "uncertainty": self.uncertainty,
            "excluded_edges": self.excluded_edges,
        }


def _hazard_segments(evidence: EvidenceAssessment | None) -> set[str]:
    if evidence and evidence.location_hazard and evidence.location_hazard.status != "NOT_INDICATED":
        return set(evidence.location_hazard.segment_ids)
    if evidence and evidence.geo:
        return set(evidence.geo.hazard_segment_ids)
    return set()


def _caution(snapshot: CitySnapshot) -> set[str]:
    return {sid for sid, status in snapshot.road_status.items() if status == "caution"}


def _alternative(projected: CitySnapshot, p: dict[str, Any], avoid: set[str]) -> dict[str, Any] | None:
    """A route between the same endpoints that is meaningfully different, or None."""
    edges = list(p["edge_path"])
    # First try avoiding the route's interior segments, so the alternative really is a different way.
    for exclude in (avoid | set(edges[1:-1]), avoid):
        alt = projected.shortest_route(p["origin"], p["destination"], exclude_edges=exclude)
        if alt is None or alt.edge_path == edges:
            continue
        overlap = len(set(alt.edge_path) & set(edges)) / max(len(set(alt.edge_path)), 1)
        if overlap > MAX_ALTERNATIVE_OVERLAP:
            continue
        extra = alt.travel_s - p["travel_s"]
        why = (f"Not selected: about {extra / 60:.1f} min slower on free-flow estimates." if extra >= 0 else
               "Faster on free-flow estimates, but the planned route was chosen under the coordinator's constraints "
               "(closures, hazard area, or junctions avoided after validation); see the plan's revisions.")
        return {"purpose": p["purpose"], "service": p.get("service"), "name": f"Alternative to {p['name']}",
                "status": "estimated_free_flow", "data_source": DATA_SOURCE, "length_m": round(alt.length_m, 1),
                "travel_s": round(alt.travel_s, 1), "extra_travel_s": round(extra, 1),
                "overlap_pct": round(overlap * 100), "not_selected_reason": why,
                "edge_path": alt.edge_path, "node_path": alt.node_path,
                "geometry": [list(c) for c in projected.graph.path_geometry(alt.node_path, alt.edge_path)]}
    return None


def adjust_plan(snapshot: CitySnapshot, actions: list[ActionSpec], evidence: EvidenceAssessment | None,
                category: str) -> tuple[list[ActionSpec], list[dict[str, Any]]]:
    """Traffic-driven changes to a draft plan. Returns the new action list and what changed and why."""
    caution = _caution(snapshot)
    hazard = _hazard_segments(evidence)
    plan_closed = {s for a in actions if a.action_type == "close_roads" for s in a.params["segment_ids"]}
    out: list[ActionSpec] = []
    changes: list[dict[str, Any]] = []
    for a in actions:
        if a.action_type in ROUTE_ACTIONS:
            p = a.params
            on_caution = [e for e in p["edge_path"] if e in caution]
            if on_caution:
                alt = snapshot.shortest_route(p["origin"], p["destination"], exclude_edges=caution | hazard | plan_closed)
                if alt is not None and (alt.travel_s <= p["travel_s"] * CAUTION_DETOUR_MAX_RATIO
                                        or alt.travel_s - p["travel_s"] <= CAUTION_DETOUR_MAX_EXTRA_S):
                    extra = alt.travel_s - p["travel_s"]
                    new_params = {**p, "edge_path": alt.edge_path, "node_path": alt.node_path,
                                  "length_m": alt.length_m, "travel_s": alt.travel_s}
                    out.append(ActionSpec(a.seq, a.action_type, a.target_id, new_params, "traffic", a.impact,
                                          f"AnalyseTraffic rerouted {p['name']} around {len(on_caution)} caution "
                                          f"segment(s) ({extra:+.0f} s estimated free-flow)."))
                    changes.append({"type": "reroute_around_caution", "purpose": p["purpose"], "name": p["name"],
                                    "caution_segments": on_caution, "extra_travel_s": round(extra, 1),
                                    "length_before_m": round(p["length_m"], 1), "length_after_m": round(alt.length_m, 1)})
                    continue
                changes.append({"type": "kept_caution_route", "purpose": p["purpose"], "name": p["name"],
                                "caution_segments": on_caution,
                                "detail": "No clear alternative within the detour limit; the route keeps the caution segments."})
        out.append(a)
    # Diversion notice for drivers when the plan closes roads (never for sensitive incident types).
    closures = [a for a in out if a.action_type == "close_roads"]
    if closures and category not in SENSITIVE_CATEGORIES and not any(a.action_type == "issue_public_advisory" for a in out):
        target = closures[0].target_id
        road = snapshot.road(target)
        name = road.name if road else "the reported location"
        n = sum(len(c.params["segment_ids"]) for c in closures)
        seq = max((a.seq for a in out), default=0) + 1
        out.append(ActionSpec(seq, "issue_public_advisory", target,
                              {"message": f"Traffic disruption near {name}: {n} road segment(s) affected. "
                                          "Avoid the area and follow diversions. (Simulation)"},
                              "traffic", "low",
                              "AnalyseTraffic: drivers need a diversion notice for the closed segments."))
        changes.append({"type": "diversion_advisory", "segments": n, "road": name})
    return out, changes


def analyse_plan(snapshot: CitySnapshot, actions: list[ActionSpec], evidence: EvidenceAssessment | None, *,
                 plan_version: int | None = None, adjustments: list[dict[str, Any]] | None = None) -> TrafficProposal:
    """Assess each route of one plan version against the state the plan itself would produce."""
    projected, _ = apply_actions(snapshot, actions)
    hazard = _hazard_segments(evidence)
    closed_now = snapshot.closed_segments()
    plan_closed = {s for a in actions if a.action_type == "close_roads" for s in a.params["segment_ids"]}
    closed = projected.closed_segments() | plan_closed
    caution = _caution(projected)
    routes = [a for a in actions if a.action_type in ROUTE_ACTIONS]

    assessments: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for r in routes:
        p = r.params
        edges = p["edge_path"]
        on_closed = sorted(e for e in edges if e in closed)
        on_hazard = sorted(e for e in edges if e in hazard and e not in closed)
        on_caution = sorted(e for e in edges if e in caution)
        status = "blocked" if on_closed or on_hazard else "caution" if on_caution else "clear"
        alt = _alternative(projected, p, closed | hazard | caution)
        risks: list[str] = []
        for nid in p["node_path"]:
            for sig in projected.signals_controlling(nid):
                state = projected.nodes[sig].status
                risks.append(f"Passes signal-controlled junction {projected.node_label(nid)} ({sig}, "
                             f"{'powered' if state == 'operational' else state} in the projected simulated state).")
        if on_caution:
            risks.append(f"Uses {len(on_caution)} segment(s) under caution.")
        if on_closed or on_hazard:
            risks.append("Blocked: uses closed segments or the modelled hazard area; the validator will reject it.")
        assessments.append({
            "purpose": p["purpose"], "service": p.get("service"), "name": p["name"], "status": status,
            "data_source": DATA_SOURCE, "travel_time_basis": "estimated free-flow (not observed traffic)",
            "length_m": round(p["length_m"], 1), "travel_s": round(p["travel_s"], 1), "segments": len(edges),
            "closed_segments": on_closed, "closed_by_this_plan": sorted(e for e in on_closed if e in plan_closed and e not in closed_now),
            "hazard_segments": on_hazard, "caution_segments": on_caution, "traffic_delay_s": None,
            "selection_reason": (f"Fastest estimated path by free-flow travel time on the one-way-aware OSM graph from "
                                 f"{p.get('origin_label', p['origin'])} to {p.get('destination_label', p['destination'])}, "
                                 "avoiding closed segments and the modelled hazard area."),
            "risks": risks,
            "confidence": "estimate: free-flow travel time, no live traffic",
            "alternative": None if alt is None else {k: alt[k] for k in ("length_m", "travel_s", "extra_travel_s", "overlap_pct",
                                                                          "not_selected_reason")},
            "alternative_note": None if alt else "No meaningfully different route exists under the plan's road state.",
        })
        if alt:
            alternatives.append(alt)
        if on_closed:
            conflicts.append(f"{p['name']} uses {len(on_closed)} segment(s) closed in the projected state.")
        if on_hazard:
            conflicts.append(f"{p['name']} enters the modelled hazard area on {len(on_hazard)} segment(s).")

    coordination: list[str] = []
    for i, left in enumerate(routes):
        for right in routes[i + 1:]:
            shared = set(left.params["edge_path"]) & set(right.params["edge_path"])
            if shared:
                coordination.append(f"{left.params['name']} and {right.params['name']} share {len(shared)} segment(s).")

    recommendations: list[dict[str, Any]] = []
    for a in (adjustments or []):
        if a["type"] == "reroute_around_caution":
            recommendations.append({"action": "reroute_applied", "status": "applied_to_plan", "purpose": a["purpose"],
                                    "detail": f"{a['name']} avoids {len(a['caution_segments'])} caution segment(s) "
                                              f"({a['extra_travel_s']:+.0f} s).",
                                    "evidence": "Simulated caution state on the route"})
        elif a["type"] == "diversion_advisory":
            recommendations.append({"action": "diversion_advisory", "status": "applied_to_plan",
                                    "detail": f"Public diversion notice added for {a['segments']} closed segment(s) near {a['road']}.",
                                    "evidence": "Closures in this plan"})
        elif a["type"] == "kept_caution_route":
            recommendations.append({"action": "caution_route_kept", "status": "operator_review", "purpose": a["purpose"],
                                    "detail": a["detail"], "evidence": "Simulated caution state on the route"})
    for r in assessments:
        if r["status"] != "blocked":
            recommendations.append({"action": "priority_corridor", "status": "simulated_recommendation",
                                    "purpose": r["purpose"],
                                    "detail": f"Keep the {r['name']} corridor clear in the simulation; no live signal change is requested.",
                                    "evidence": "Route geometry and estimated free-flow travel time from the OSM graph"})
    if coordination:
        recommendations.append({"action": "route_coordination", "status": "simulated_recommendation",
                                "detail": " ".join(coordination) + " Coordinate responder priority on shared segments.",
                                "evidence": "Overlap between route geometries in this plan"})

    uncertainty = [
        "No live traffic feed is configured; current congestion, queue length, signal timing and incident delay are unavailable.",
        "Travel times are free-flow estimates from the versioned OSM road graph, not observations.",
    ]
    if evidence and evidence.location_hazard and evidence.location_hazard.status not in ("CORROBORATED", "OPERATOR_CONFIRMED"):
        uncertainty.append(f"The location hazard is {evidence.location_hazard.status}; disruption around it is estimated from the report.")
    if evidence and evidence.geo and not evidence.geo.in_coverage:
        uncertainty.append("The report is outside the mapped coverage area, so traffic impacts cannot be modelled.")

    sources = [
        {"source": "OpenStreetMap road graph", "type": "static map data",
         "freshness": snapshot.graph.meta.get("osm_base_timestamp") or "versioned snapshot"},
        {"source": "GridPulse simulated closures, cautions and signal power", "type": "simulated",
         "freshness": "current simulated city state at analysis time"},
        {"source": "Live traffic (speeds, congestion, queues)", "type": "unavailable",
         "freshness": "no feed configured"},
    ]
    if evidence and evidence.location_hazard:
        sources.append({"source": f"Hazard evidence: {evidence.location_hazard.status.lower().replace('_', ' ')}",
                        "type": "operator-confirmed" if evidence.location_hazard.status == "OPERATOR_CONFIRMED"
                        else "synthetic sensors" if evidence.observations else "citizen report",
                        "freshness": evidence.assessed_at.isoformat()})
    hazard_nodes = {n for sid in hazard for n in (snapshot.graph.segments[sid].u, snapshot.graph.segments[sid].v)
                    if sid in snapshot.graph.segments}

    return TrafficProposal(
        evidence_sources=sources,
        affected_junctions=sorted(hazard_nodes),
        data_source="estimated", live_traffic=LIVE_TRAFFIC,
        freshness=snapshot.graph.meta.get("osm_base_timestamp") or "versioned road-graph snapshot",
        affected_segments=sorted(hazard | closed | caution), route_assessments=assessments,
        alternative_routes=alternatives, conflicts=conflicts, traffic_management_recommendations=recommendations,
        assumptions=["The incident pin is the location to analyse.",
                     "The plan's own closures and cautions are applied before routes are assessed.",
                     "Traffic-management recommendations are simulated guidance, not real signal or road-control commands."],
        uncertainty=uncertainty, excluded_edges=sorted(hazard | closed), plan_version=plan_version,
        state_version=snapshot.state_version, adjustments=list(adjustments or []),
    )


def failed_context(reason: str, plan_version: int | None, state_version: int) -> dict[str, Any]:
    """What validation sees when the traffic agent could not run: routes stay validated by R1/R2 alone."""
    return {"status": "failed", "error": reason[:200], "live_traffic": LIVE_TRAFFIC, "data_source": None,
            "freshness": None, "plan_version": plan_version, "state_version": state_version,
            "route_assessments": [], "conflicts": []}


def analyse(snapshot: CitySnapshot, assessment: SentinelAssessment, evidence: EvidenceAssessment, geo: IncidentGeo,
            mobility: Any) -> TrafficProposal:
    """Assess Mobility's proposed routes on their own (kept for callers that have no assembled plan)."""
    from app.orchestration.revision import make_action  # local import: revision imports agents

    actions = [make_action(i, a) for i, a in enumerate(mobility.actions, start=1)]
    return analyse_plan(snapshot, actions, evidence)

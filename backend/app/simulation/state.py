"""In-memory snapshot of the city: the real road graph plus the simulated infrastructure overlay.

Two layers, deliberately kept apart:
  * Road layer: a RoadGraph built from OpenStreetMap (real geography). Dynamic road state
    (closed / caution) is a dict of overrides keyed by segment id.
  * Infrastructure overlay (SIMULATED): feeder zones, the power model of real OSM traffic signals,
    and scenario sensors. Stored as NodeState / EdgeState objects.

Everything safety-relevant (routing, power dependencies, projected effects of actions) lives here
as plain Python + NetworkX so the validator, the agents and the execution engine all share one
implementation and can be unit-tested without a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

from app.geo.road_graph import RoadGraph


@dataclass
class NodeState:
    id: str
    kind: str  # substation (simulated feeder zone) | signal | sensor
    name: str
    zone: str
    lat: float
    lon: float
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class EdgeState:
    id: str
    kind: str  # controls | power_feed
    source: str
    target: str
    name: str
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionSpec:
    """A plan action as the validator and engine see it (independent of ORM rows)."""

    seq: int
    action_type: str
    target_id: str
    params: dict[str, Any]
    proposed_by: str = "unknown"
    impact: str = "high"
    rationale: str = ""


@dataclass(frozen=True)
class RoadView:
    id: str
    name: str
    status: str
    length_m: float
    source: str
    target: str


@dataclass
class RouteResult:
    origin: str
    destination: str
    edge_path: list[str]
    node_path: list[str]
    length_m: float
    travel_s: float
    signal_dependencies: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin, "destination": self.destination, "edge_path": self.edge_path,
            "node_path": self.node_path, "length_m": self.length_m, "travel_s": self.travel_s,
            "signal_dependencies": self.signal_dependencies,
        }


ENERGIZED = "energized"
ROAD_ACTIONS_CLOSE = "close_roads"
ROAD_ACTIONS_CAUTION = "set_roads_caution"


class CitySnapshot:
    def __init__(
        self,
        state_version: int,
        epoch: int,
        graph: RoadGraph,
        road_status: dict[str, str],
        nodes: dict[str, NodeState],
        edges: dict[str, EdgeState],
        city_name: str = "Mangaluru",
    ):
        self.state_version = state_version
        self.epoch = epoch
        self.graph = graph
        self.road_status = road_status
        self.nodes = nodes
        self.edges = edges
        self.city_name = city_name
        self._controls: dict[str, list[str]] = defaultdict(list)
        self._feeds: dict[str, list[str]] = defaultdict(list)
        for e in edges.values():
            if e.kind == "controls":
                self._controls[e.target].append(e.id)
            elif e.kind == "power_feed":
                self._feeds[e.target].append(e.id)

    def clone(self) -> CitySnapshot:
        return CitySnapshot(
            self.state_version, self.epoch, self.graph, dict(self.road_status),
            {k: replace(v, attributes=dict(v.attributes)) for k, v in self.nodes.items()},
            {k: replace(v, attributes=dict(v.attributes)) for k, v in self.edges.items()},
            self.city_name,
        )

    # ---- road layer ---------------------------------------------------------------------------
    def road(self, seg_id: str) -> RoadView | None:
        seg = self.graph.segments.get(seg_id)
        if seg is None:
            return None
        return RoadView(seg.id, seg.label, self.road_status.get(seg_id, "open"), seg.length_m, seg.u, seg.v)

    def closed_segments(self) -> set[str]:
        return {s for s, st in self.road_status.items() if st == "closed"}

    def node_label(self, nid: str) -> str:
        if nid in self.nodes:
            return self.nodes[nid].name
        return self.graph.node_label(nid)

    def has_road_node(self, nid: str) -> bool:
        return nid in self.graph.nodes

    # ---- overlay lookups ----------------------------------------------------------------------
    def edges_of_kind(self, kind: str) -> list[EdgeState]:
        return [e for e in self.edges.values() if e.kind == kind]

    def signals_controlling(self, intersection_id: str) -> list[str]:
        return sorted(self.edges[e].source for e in self._controls.get(intersection_id, ())
                      if self.edges[e].status == "active")

    def feeds_for(self, consumer_id: str) -> list[EdgeState]:
        return sorted((self.edges[e] for e in self._feeds.get(consumer_id, ())), key=lambda e: e.id)

    def is_powered(self, consumer_id: str) -> bool:
        return any(f.status == "active" and self.nodes[f.source].status == ENERGIZED for f in self.feeds_for(consumer_id))

    def recompute_derived(self) -> None:
        """Signals follow their (simulated) power supply."""
        for node in self.nodes.values():
            if node.kind == "signal":
                node.status = "operational" if self.is_powered(node.id) else "dark"

    def dependents_of(self, substation_id: str) -> list[str]:
        """Consumers that lose power if the feeder zone is lost, considering only active feeds."""
        affected: list[str] = []
        for feed in self.edges_of_kind("power_feed"):
            if feed.source != substation_id or feed.status != "active":
                continue
            others = [f for f in self.feeds_for(feed.target)
                      if f.id != feed.id and f.status == "active" and self.nodes[f.source].status == ENERGIZED]
            if not others:
                affected.append(feed.target)
        return sorted(set(affected))

    def route_signal_dependencies(self, node_path: list[str]) -> list[dict[str, Any]]:
        deps = []
        for nid in node_path:
            for sig in self.signals_controlling(nid):
                active_feeds = [f for f in self.feeds_for(sig) if f.status == "active"]
                deps.append({"intersection": nid, "signal": sig, "signal_status": self.nodes[sig].status,
                             "powered_by": [f.source for f in active_feeds], "feeds": [f.id for f in active_feeds]})
        return deps

    # ---- routing ------------------------------------------------------------------------------
    def shortest_route(
        self,
        origin: str,
        destination: str,
        exclude_edges: set[str] | None = None,
        exclude_nodes: set[str] | None = None,
    ) -> RouteResult | None:
        blocked = self.closed_segments() | set(exclude_edges or ())
        r = self.graph.shortest_route(origin, destination, exclude_edges=blocked, exclude_nodes=set(exclude_nodes or ()))
        if r is None:
            return None
        return RouteResult(r.origin, r.destination, r.edge_path, r.node_path, r.length_m, r.travel_s,
                           self.route_signal_dependencies(r.node_path))


def _segment_ids(a: ActionSpec) -> list[str]:
    ids = a.params.get("segment_ids")
    return list(ids) if isinstance(ids, list) else []


def apply_actions(snapshot: CitySnapshot, actions: list[ActionSpec]) -> tuple[CitySnapshot, list[dict[str, Any]]]:
    """Return a projected copy of the city after `actions`, plus a list of concrete state changes.

    Pure function: used by the validator (to predict consequences) and by the execution engine
    (to compute exactly what to persist). Unknown action types are ignored here; the validator
    rejects them before anything is executed.
    """
    projected = snapshot.clone()
    for a in sorted(actions, key=lambda a: a.seq):
        if a.action_type == ROAD_ACTIONS_CLOSE:
            for sid in _segment_ids(a):
                if sid in projected.graph.segments:
                    projected.road_status[sid] = "closed"
        elif a.action_type == ROAD_ACTIONS_CAUTION:
            for sid in _segment_ids(a):
                if sid in projected.graph.segments and projected.road_status.get(sid) != "closed":
                    projected.road_status[sid] = "caution"
        elif a.action_type == "isolate_substation" and a.target_id in projected.nodes:
            projected.nodes[a.target_id].status = "isolated"
        elif a.action_type == "transfer_signal_power":
            src, dst = a.params.get("from_feed"), a.params.get("to_feed")
            if src in projected.edges and dst in projected.edges:
                projected.edges[src].status = "standby"
                projected.edges[dst].status = "active"
        projected.recompute_derived()

    changes: list[dict[str, Any]] = []
    for sid in sorted(set(projected.road_status) | set(snapshot.road_status)):
        before, after = snapshot.road_status.get(sid, "open"), projected.road_status.get(sid, "open")
        if before != after:
            changes.append({"entity": "road", "id": sid, "name": snapshot.graph.segments[sid].label,
                            "kind": "road", "before": before, "after": after})
    for nid, node in projected.nodes.items():
        before = snapshot.nodes[nid].status
        if node.status != before:
            changes.append({"entity": "node", "id": nid, "name": node.name, "kind": node.kind, "before": before, "after": node.status})
    for eid, edge in projected.edges.items():
        before = snapshot.edges[eid].status
        if edge.status != before:
            changes.append({"entity": "edge", "id": eid, "name": edge.name, "kind": edge.kind, "before": before, "after": edge.status})
    return projected, changes

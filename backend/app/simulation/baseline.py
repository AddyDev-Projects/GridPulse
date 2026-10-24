"""Build a CitySnapshot without a database: the real Mangaluru graph plus the simulated overlay."""

from app.geo.road_graph import RoadGraph, mangaluru_graph
from app.simulation.infrastructure import build_overlay
from app.simulation.state import CitySnapshot


def baseline_snapshot(graph: RoadGraph | None = None, state_version: int = 1, epoch: int = 1) -> CitySnapshot:
    graph = graph or mangaluru_graph()
    nodes, edges = build_overlay(graph)
    snap = CitySnapshot(state_version, epoch, graph, {}, {n.id: n for n in nodes}, {e.id: e for e in edges})
    snap.recompute_derived()
    return snap

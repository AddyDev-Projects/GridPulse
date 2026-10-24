"""SIMULATED infrastructure-dependency model laid over the real Mangaluru road graph.

What is real and what is not:
  * Traffic-signal locations come from OpenStreetMap (highway=traffic_signals). Real.
  * Feeder zones, which zone powers which signal, and backup feeds are ILLUSTRATIVE. GridPulse has
    no access to MESCOM or any utility data, so the power network is a deterministic synthetic
    model: the road graph's bounding box is split into a 3x3 grid of "simulated feeder zones";
    each signal is fed by the zone containing it, with a standby backup feed from the nearest
    other zone.
Every node and edge produced here carries attributes["simulated"] = True and the UI labels it.
"""

from __future__ import annotations

from app.geo.osm import haversine_m
from app.geo.road_graph import RoadGraph
from app.simulation.state import EdgeState, NodeState

ROWS, COLS = 3, 3
COL_NAMES = "ABC"


def zone_id(row: int, col: int) -> str:
    return f"SIM-FZ-{row + 1}{COL_NAMES[col]}"


def zone_of(graph: RoadGraph, lat: float, lon: float) -> str:
    s, w, n, e = graph.bbox
    row = min(ROWS - 1, max(0, int((lat - s) / ((n - s) / ROWS))))
    col = min(COLS - 1, max(0, int((lon - w) / ((e - w) / COLS))))
    return zone_id(row, col)


def zone_bounds(graph: RoadGraph, zid: str) -> tuple[float, float, float, float]:
    s, w, n, e = graph.bbox
    row, col = int(zid[-2]) - 1, COL_NAMES.index(zid[-1])
    dlat, dlon = (n - s) / ROWS, (e - w) / COLS
    return (s + row * dlat, w + col * dlon, s + (row + 1) * dlat, w + (col + 1) * dlon)


def build_overlay(graph: RoadGraph) -> tuple[list[NodeState], list[EdgeState]]:
    nodes: list[NodeState] = []
    edges: list[EdgeState] = []
    centers: dict[str, tuple[float, float]] = {}
    for r in range(ROWS):
        for c in range(COLS):
            zid = zone_id(r, c)
            s, w, n, e = zone_bounds(graph, zid)
            centers[zid] = ((s + n) / 2, (w + e) / 2)
            nodes.append(NodeState(zid, "substation", f"Simulated feeder zone {zid[-2:]}", zid, centers[zid][0],
                                   centers[zid][1], "energized",
                                   {"simulated": True, "bounds": [s, w, n, e],
                                    "note": "Illustrative grid cell, not a real substation or MESCOM feeder."}))
    for osm_node in sorted(graph.signals):
        lat, lon = graph.nodes[osm_node]
        sid = f"SIG-{osm_node}"
        zid = zone_of(graph, lat, lon)
        backup = min((z for z in centers if z != zid), key=lambda z: haversine_m(lat, lon, *centers[z]))
        nodes.append(NodeState(sid, "signal", f"Traffic signal, {graph.node_label(osm_node)}", zid, lat, lon,
                               "operational", {"osm_node": osm_node, "location_source": "OpenStreetMap",
                                               "power_model": "simulated", "simulated": True}))
        edges.append(EdgeState(f"C-{sid}", "controls", sid, osm_node, f"{sid} controls {osm_node}", "active",
                               {"simulated": False, "note": "Signal location and junction are from OSM."}))
        edges.append(EdgeState(f"F-{zid}-{sid}", "power_feed", zid, sid, f"{zid} → {sid} primary feed (simulated)",
                               "active", {"role": "primary", "simulated": True}))
        edges.append(EdgeState(f"F-{backup}-{sid}", "power_feed", backup, sid, f"{backup} → {sid} backup feed (simulated)",
                               "standby", {"role": "backup", "simulated": True}))
    return nodes, edges

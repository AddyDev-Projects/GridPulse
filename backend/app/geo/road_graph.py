"""Load the Mangaluru road graph and answer routing / spatial queries on it.

Immutable once loaded and shared by every request. Dynamic state (closures, simulated power)
lives in the database and is applied per query through exclusions, never by mutating the graph.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx

from app.geo.osm import GRAPH_FILE, content_sha256, haversine_m


class GraphDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    id: str
    u: str
    v: str
    oneway: bool
    highway: str
    name: str | None
    length_m: float
    travel_s: float
    geometry: tuple[tuple[float, float], ...]

    @property
    def label(self) -> str:
        return self.name or f"Unnamed {self.highway.replace('_', ' ')} road"


@dataclass(frozen=True)
class Poi:
    id: str
    kind: str
    name: str | None
    lat: float
    lon: float

    @property
    def label(self) -> str:
        return self.name or f"{self.kind.replace('_', ' ').title()} (unnamed in OSM, {self.id})"


@dataclass
class Route:
    origin: str
    destination: str
    node_path: list[str]
    edge_path: list[str]
    length_m: float
    travel_s: float


@dataclass
class RoadGraph:
    meta: dict[str, Any]
    nodes: dict[str, tuple[float, float]]
    segments: dict[str, Segment]
    signals: frozenset[str]
    pois: list[Poi]
    bbox: tuple[float, float, float, float]
    G: nx.MultiDiGraph = field(repr=False)
    _cells: dict[tuple[int, int], list[str]] = field(default_factory=dict, repr=False)
    _seg_cells: dict[tuple[int, int], list[str]] = field(default_factory=dict, repr=False)
    _node_segments: dict[str, list[str]] = field(default_factory=dict, repr=False)

    CELL = 0.004  # degrees, ~440 m

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, verify: bool = True) -> RoadGraph:
        meta = data.get("meta", {})
        if verify and meta.get("sha256") and content_sha256(data) != meta["sha256"]:
            raise GraphDataError("Road graph file failed its checksum; rebuild it with `python -m app.cli geodata fetch`.")
        nodes = {k: (v[0], v[1]) for k, v in data["nodes"].items()}
        segments = {}
        G = nx.MultiDiGraph()
        G.add_nodes_from(nodes)
        for s in data["segments"]:
            seg = Segment(s["id"], s["u"], s["v"], s["oneway"], s["highway"], s.get("name"), s["length_m"],
                          s["travel_s"], tuple((p[0], p[1]) for p in s["geometry"]))
            segments[seg.id] = seg
            G.add_edge(seg.u, seg.v, key=seg.id, travel_s=seg.travel_s)
            if not seg.oneway:
                G.add_edge(seg.v, seg.u, key=seg.id, travel_s=seg.travel_s)
        g = cls(meta, nodes, segments, frozenset(data.get("signals", [])),
                [Poi(**p) for p in data.get("pois", [])], tuple(data["bbox"]), G)
        g._index()
        return g

    @classmethod
    def load(cls, path: Path = GRAPH_FILE) -> RoadGraph:
        if not path.exists():
            raise GraphDataError(f"Road graph file {path.name} not found; run `python -m app.cli geodata fetch`.")
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ---- spatial index ------------------------------------------------------------------------
    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return (int(lat / self.CELL), int(lon / self.CELL))

    def _index(self) -> None:
        cells: dict[tuple[int, int], list[str]] = defaultdict(list)
        for nid, (lat, lon) in self.nodes.items():
            cells[self._cell(lat, lon)].append(nid)
        seg_cells: dict[tuple[int, int], list[str]] = defaultdict(list)
        node_segments: dict[str, list[str]] = defaultdict(list)
        for seg in self.segments.values():
            for c in {self._cell(lat, lon) for lat, lon in seg.geometry}:
                seg_cells[c].append(seg.id)
            node_segments[seg.u].append(seg.id)
            node_segments[seg.v].append(seg.id)
        self._cells, self._seg_cells, self._node_segments = dict(cells), dict(seg_cells), dict(node_segments)

    def in_bbox(self, lat: float, lon: float) -> bool:
        s, w, n, e = self.bbox
        return s <= lat <= n and w <= lon <= e

    def _nearby_cells(self, lat: float, lon: float, radius_m: float) -> list[tuple[int, int]]:
        r = max(1, math.ceil(radius_m / 111_000 / self.CELL))
        ci, cj = self._cell(lat, lon)
        return [(ci + di, cj + dj) for di in range(-r, r + 1) for dj in range(-r, r + 1)]

    def nearest_node(self, lat: float, lon: float, max_m: float = 1500.0) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for c in self._nearby_cells(lat, lon, max_m):
            for nid in self._cells.get(c, ()):
                d = haversine_m(lat, lon, *self.nodes[nid])
                if d <= max_m and (best is None or d < best[1]):
                    best = (nid, d)
        return best

    def segments_within(self, lat: float, lon: float, radius_m: float) -> list[str]:
        """Segments with any geometry point (or segment interior) within radius_m of the point."""
        found = []
        for sid in {s for c in self._nearby_cells(lat, lon, radius_m) for s in self._seg_cells.get(c, ())}:
            if _distance_to_polyline_m(lat, lon, self.segments[sid].geometry) <= radius_m:
                found.append(sid)
        return sorted(found)

    def nearest_segment(self, lat: float, lon: float, max_m: float = 300.0) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for sid in {s for c in self._nearby_cells(lat, lon, max_m) for s in self._seg_cells.get(c, ())}:
            d = _distance_to_polyline_m(lat, lon, self.segments[sid].geometry)
            if d <= max_m and (best is None or d < best[1]):
                best = (sid, d)
        return best

    def segments_at(self, node: str) -> list[str]:
        return self._node_segments.get(node, [])

    def segments_in_bbox(self, s: float, w: float, n: float, e: float, limit: int) -> list[str]:
        cells = {(i, j) for i in range(int(s / self.CELL), int(n / self.CELL) + 1)
                 for j in range(int(w / self.CELL), int(e / self.CELL) + 1)}
        out: list[str] = []
        for c in cells:
            for sid in self._seg_cells.get(c, ()):
                out.append(sid)
        out = sorted(set(out))
        return out[:limit]

    def node_label(self, nid: str) -> str:
        names = sorted({self.segments[s].name for s in self.segments_at(nid) if self.segments[s].name})
        if len(names) >= 2:
            return f"Junction of {names[0]} and {names[1]}"
        if names:
            return f"Point on {names[0]}"
        return f"Intersection {nid} (unnamed roads)"

    # ---- routing ------------------------------------------------------------------------------
    def shortest_route(self, origin: str, destination: str, *, exclude_edges: set[str] | frozenset[str] = frozenset(),
                       exclude_nodes: set[str] | frozenset[str] = frozenset()) -> Route | None:
        """Fastest path by estimated travel time, honouring one-way restrictions and exclusions.

        Returns None when either endpoint is unknown or no path exists (e.g. disconnected component).
        """
        if origin not in self.G or destination not in self.G:
            return None
        blocked_nodes = set(exclude_nodes) - {origin, destination}

        def weight(u: str, v: str, keyed: dict[str, dict[str, Any]]) -> float | None:
            if v in blocked_nodes or u in blocked_nodes:
                return None
            allowed = [d["travel_s"] for k, d in keyed.items() if k not in exclude_edges]
            return min(allowed) if allowed else None

        try:
            node_path = nx.dijkstra_path(self.G, origin, destination, weight=weight)
        except nx.NetworkXNoPath:
            return None
        edge_path = []
        for a, b in zip(node_path, node_path[1:]):
            options = [(d["travel_s"], k) for k, d in self.G[a][b].items() if k not in exclude_edges]
            edge_path.append(min(options)[1])
        return Route(origin, destination, node_path, edge_path,
                     round(sum(self.segments[e].length_m for e in edge_path), 1),
                     round(sum(self.segments[e].travel_s for e in edge_path), 1))

    def path_geometry(self, node_path: list[str], edge_path: list[str]) -> list[tuple[float, float]]:
        """Concatenate segment geometries in travel direction (reversed where traversed v→u)."""
        coords: list[tuple[float, float]] = []
        for a, sid in zip(node_path, edge_path):
            geom = list(self.segments[sid].geometry)
            if self.segments[sid].u != a:
                geom.reverse()
            coords.extend(geom if not coords else geom[1:])
        return coords

    def is_continuous(self, node_path: list[str], edge_path: list[str]) -> list[str]:
        """Problems that make edge_path not a valid traversal of node_path (direction-aware)."""
        problems = []
        if len(edge_path) != len(node_path) - 1:
            return ["Edge and node paths have inconsistent lengths."]
        for a, b, sid in zip(node_path, node_path[1:], edge_path):
            seg = self.segments.get(sid)
            if seg is None:
                problems.append(f"Segment '{sid}' is not in the road graph.")
            elif not (self.G.has_edge(a, b, key=sid)):
                problems.append(f"Segment {sid} cannot be travelled from {a} to {b} (wrong endpoints or one-way).")
        return problems


def _distance_to_polyline_m(lat: float, lon: float, geom: tuple[tuple[float, float], ...]) -> float:
    """Approximate point-to-polyline distance with a local equirectangular projection."""
    kx = 111_320 * math.cos(math.radians(lat))
    ky = 110_540
    px, py = 0.0, 0.0
    best = float("inf")
    pts = [((g[1] - lon) * kx, (g[0] - lat) * ky) for g in geom]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x1 + t * dx - px, y1 + t * dy - py))
    return best


@lru_cache(maxsize=1)
def mangaluru_graph() -> RoadGraph:
    return RoadGraph.load()

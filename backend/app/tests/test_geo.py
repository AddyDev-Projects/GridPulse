"""Real Mangaluru road graph: integrity, connectivity, direction-aware routing, closures, no-path cases.

These run against the committed OSM-derived graph file (no network, no database).
"""

import gzip
import json

import networkx as nx
import pytest

from app.geo.osm import GRAPH_FILE, build_graph, haversine_m
from app.geo.road_graph import GraphDataError, RoadGraph, mangaluru_graph


@pytest.fixture(scope="module")
def g() -> RoadGraph:
    return mangaluru_graph()


def test_graph_is_mangaluru_from_osm_and_checksummed(g):
    assert g.meta["city"] == "Mangaluru" and g.meta["country"] == "India"
    assert "OpenStreetMap" in g.meta["source"] and "ODbL" in g.meta["license"]
    s, w, n, e = g.bbox
    assert 12.8 < s < n < 13.0 and 74.8 < w < e < 74.95, "coverage box is central Mangaluru"
    assert len(g.nodes) > 5000 and len(g.segments) > 8000
    with gzip.open(GRAPH_FILE, "rt", encoding="utf-8") as f:
        data = json.load(f)
    data["segments"][0]["length_m"] += 1  # tamper
    with pytest.raises(GraphDataError):
        RoadGraph.from_dict(data)


def test_largest_component_dominates_but_fragments_exist(g):
    comps = sorted((len(c) for c in nx.strongly_connected_components(g.G)), reverse=True)
    assert comps[0] / len(g.nodes) > 0.95
    assert len(comps) > 1, "real OSM extracts contain disconnected fragments; routing must cope"


def test_segment_geometry_and_lengths_are_consistent(g):
    for seg in list(g.segments.values())[:500]:
        assert g.nodes[seg.u] == seg.geometry[0] and g.nodes[seg.v] == seg.geometry[-1]
        approx = sum(haversine_m(*seg.geometry[i], *seg.geometry[i + 1]) for i in range(len(seg.geometry) - 1))
        assert abs(approx - seg.length_m) < 1.0


def test_route_is_continuous_and_geometry_matches_edges(g):
    a, b = sorted(g.nodes)[100], sorted(g.nodes)[4000]
    r = g.shortest_route(a, b)
    assert r is not None and r.node_path[0] == a and r.node_path[-1] == b
    assert g.is_continuous(r.node_path, r.edge_path) == []
    geom = g.path_geometry(r.node_path, r.edge_path)
    assert geom[0] == g.nodes[a] and geom[-1] == g.nodes[b]
    assert abs(sum(g.segments[e].length_m for e in r.edge_path) - r.length_m) < 1


def test_one_way_segments_cannot_be_driven_backwards(g):
    seg = next(s for s in g.segments.values() if s.oneway and s.u != s.v)
    assert g.G.has_edge(seg.u, seg.v, key=seg.id)
    assert not g.G.has_edge(seg.v, seg.u, key=seg.id)
    problems = g.is_continuous([seg.v, seg.u], [seg.id])
    assert problems and "one-way" in problems[0]


def test_routing_avoids_a_closed_segment_when_an_alternative_exists(g):
    a, b = sorted(g.nodes)[200], sorted(g.nodes)[3000]
    first = g.shortest_route(a, b)
    blocked = first.edge_path[len(first.edge_path) // 2]
    second = g.shortest_route(a, b, exclude_edges={blocked})
    assert second is not None and blocked not in second.edge_path
    assert second.travel_s >= first.travel_s


def test_no_route_is_reported_not_invented(g):
    comps = sorted(nx.strongly_connected_components(g.G), key=len)
    small, big = next(iter(comps[0])), next(iter(comps[-1]))
    assert g.shortest_route(small, big) is None or small in comps[-1]
    assert g.shortest_route("n0", big) is None, "unknown node"
    a = sorted(g.nodes)[200]
    cut = {s for s in g.segments_at(a)}
    assert g.shortest_route(a, big, exclude_edges=cut) is None


def test_spatial_queries(g):
    seg = g.segments[sorted(g.segments)[50]]
    lat, lon = seg.geometry[0]
    near = g.nearest_segment(lat, lon, 50)
    assert near is not None and near[1] < 1.0
    assert seg.id in g.segments_within(lat, lon, 30)
    assert g.nearest_node(0.0, 0.0) is None
    assert not g.in_bbox(12.0, 74.0)


def test_builder_honours_oneway_tags_and_drops_private_roads():
    osm = {"elements": [
        {"type": "node", "id": 1, "lat": 12.87, "lon": 74.84}, {"type": "node", "id": 2, "lat": 12.871, "lon": 74.84},
        {"type": "node", "id": 3, "lat": 12.872, "lon": 74.84},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "residential", "oneway": "-1", "name": "Test Rd"}},
        {"type": "way", "id": 11, "nodes": [2, 3], "tags": {"highway": "service", "access": "private"}},
    ]}
    built = build_graph(osm, (12.8, 74.8, 12.9, 74.9))
    [seg] = built["segments"]
    assert seg["oneway"] and seg["u"] == "n2" and seg["v"] == "n1", "oneway=-1 is reversed"

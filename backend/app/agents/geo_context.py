"""Deterministic geographic context for a report: snap to the real road graph, model the hazard area.

The hazard radius is a stated modelling assumption per category (not a measurement); it decides
which real road segments Mobility proposes to close.
"""

from __future__ import annotations

from app.schemas.agents import IncidentGeo
from app.simulation.infrastructure import zone_of
from app.simulation.state import CitySnapshot

HAZARD_RADIUS_M = {"flood": 150.0, "fire": 80.0, "road_obstruction": 30.0, "power_outage": 0.0, "other": 0.0}
RISING_FACTOR = 1.3
MAX_SNAP_M = 400.0


def hazard_radius(category: str, indicators: list[str]) -> float:
    r = HAZARD_RADIUS_M.get(category, 0.0)
    if r and "water_rising" in indicators and category == "flood":
        r *= RISING_FACTOR
    return round(r, 1)


def incident_geo(snapshot: CitySnapshot, lat: float, lon: float, category: str, indicators: list[str]) -> IncidentGeo:
    g = snapshot.graph
    covered = g.in_bbox(lat, lon)
    seg = g.nearest_segment(lat, lon, MAX_SNAP_M) if covered else None
    node = g.nearest_node(lat, lon, MAX_SNAP_M) if covered else None
    radius = hazard_radius(category, indicators) if seg else 0.0
    hazard_segments = g.segments_within(lat, lon, radius) if radius else []
    if seg and radius and seg[0] not in hazard_segments:
        hazard_segments = sorted(set(hazard_segments) | {seg[0]})
    return IncidentGeo(
        lat=lat, lon=lon, in_coverage=covered,
        segment_id=seg[0] if seg else None,
        segment_name=g.segments[seg[0]].label if seg else None,
        snap_distance_m=round(seg[1], 1) if seg else None,
        node_id=node[0] if node else None,
        zone_id=zone_of(g, lat, lon) if covered else None,
        hazard_radius_m=radius,
        hazard_segment_ids=hazard_segments,
    )

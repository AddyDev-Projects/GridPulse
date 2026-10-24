"""Persistence for city state: the City entity, graph versions, overlay, closures and demo scenarios.

Startup only registers reference data (the City, the active road-graph version and the simulated
infrastructure overlay). It never creates incidents, sensors or closures; demo scenarios are loaded
explicitly. Nothing in this module deletes rows: closures are lifted, sensors are taken offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.geo_context import incident_geo
from app.db.base import utcnow
from app.geo.road_graph import RoadGraph, mangaluru_graph
from app.models import (
    City,
    InfrastructureEdge,
    InfrastructureNode,
    RoadClosure,
    RoadGraphVersion,
    SensorObservation,
    SimulationMeta,
    SimulationRoute,
)
from app.models.simulation import DATASET_CURRENT
from app.simulation.infrastructure import build_overlay, zone_bounds
from app.simulation.scenarios import SCENARIOS, Scenario, nearest_hazard_segments
from app.simulation.state import CitySnapshot, EdgeState, NodeState

NO_SCENARIO = "none"


class SimulationNotInitialized(RuntimeError):
    pass


class ScenarioError(RuntimeError):
    pass


def get_meta(db: Session, *, for_update: bool = False) -> SimulationMeta:
    stmt = select(SimulationMeta).where(SimulationMeta.id == 1)
    if for_update:
        stmt = stmt.with_for_update()
    meta = db.scalars(stmt).first()
    if meta is None or meta.city_id is None:
        raise SimulationNotInitialized("City state is not initialised; start the backend once to register Mangaluru.")
    return meta


def get_city(db: Session) -> City:
    city = db.scalars(select(City).where(City.name == "Mangaluru")).first()
    if city is None:
        raise SimulationNotInitialized("City 'Mangaluru' is not registered.")
    return city


def ensure_initialized(db: Session, graph: RoadGraph | None = None) -> None:
    """Idempotent: register Mangaluru, its active graph version and the simulated overlay."""
    graph = graph or mangaluru_graph()
    meta_g = graph.meta
    s, w, n, e = graph.bbox
    city = db.scalars(select(City).where(City.name == "Mangaluru")).first()
    if city is None:
        city = City(name="Mangaluru", state=meta_g.get("state", "Karnataka"), country=meta_g.get("country", "India"),
                    country_code=meta_g.get("country_code", "IN"), center_lat=(s + n) / 2, center_lon=(w + e) / 2,
                    coverage_bbox=[s, w, n, e])
        db.add(city)
        db.flush()

    sha = meta_g.get("sha256", "")
    version = db.scalars(select(RoadGraphVersion).where(RoadGraphVersion.sha256 == sha)).first()
    graph_changed = False
    if version is None or not version.active:
        db.execute(update(RoadGraphVersion).where(RoadGraphVersion.city_id == city.id, RoadGraphVersion.active.is_(True))
                   .values(active=False))
        db.flush()
        if version is None:
            version = RoadGraphVersion(city_id=city.id, sha256=sha, file_name="mangaluru_roads.json.gz",
                                       source=meta_g.get("source", "OpenStreetMap"), license=meta_g.get("license", "ODbL"),
                                       osm_base_timestamp=meta_g.get("osm_base_timestamp"),
                                       node_count=len(graph.nodes), segment_count=len(graph.segments),
                                       signal_count=len(graph.signals), poi_count=len(graph.pois), active=True)
            db.add(version)
        else:
            version.active = True
        graph_changed = True

    nodes, edges = build_overlay(graph)
    existing_nodes = {r.id for r in db.scalars(select(InfrastructureNode).where(InfrastructureNode.dataset == DATASET_CURRENT))}
    junctions = {ed.target for ed in edges if ed.kind == "controls"}
    for jid in sorted(junctions - existing_nodes):
        lat, lon = graph.nodes[jid]
        db.add(InfrastructureNode(id=jid, kind="intersection", name=graph.node_label(jid), zone="road-graph", x=lon, y=lat,
                                  lat=lat, lon=lon, dataset=DATASET_CURRENT, status="open",
                                  attributes={"osm_node": jid, "simulated": False}))
    for nd in nodes:
        if nd.id not in existing_nodes:
            db.add(_node_row(nd))
    db.flush()
    existing_edges = {r.id for r in db.scalars(select(InfrastructureEdge).where(InfrastructureEdge.dataset == DATASET_CURRENT))}
    for ed in edges:
        if ed.id not in existing_edges:
            db.add(InfrastructureEdge(id=ed.id, kind=ed.kind, source_id=ed.source, target_id=ed.target, name=ed.name,
                                      dataset=DATASET_CURRENT, status=ed.status, attributes=dict(ed.attributes)))
    db.flush()

    meta = db.scalars(select(SimulationMeta).where(SimulationMeta.id == 1).with_for_update()).first()
    if meta is None:
        db.add(SimulationMeta(id=1, state_version=1, epoch=1, active_scenario=NO_SCENARIO, city_id=city.id))
    elif meta.city_id is None or graph_changed:
        # First start on Mangaluru (legacy fictional-city plans become stale) or a new road-graph version.
        meta.city_id = city.id
        meta.state_version += 1
        meta.epoch += 1
        meta.active_scenario = NO_SCENARIO
        meta.updated_at = utcnow()
    db.flush()


def _node_row(nd: NodeState) -> InfrastructureNode:
    return InfrastructureNode(id=nd.id, kind=nd.kind, name=nd.name[:160], zone=nd.zone, x=nd.lon, y=nd.lat, lat=nd.lat,
                              lon=nd.lon, dataset=DATASET_CURRENT, status=nd.status, attributes=dict(nd.attributes))


def load_snapshot(db: Session, meta: SimulationMeta | None = None, graph: RoadGraph | None = None) -> CitySnapshot:
    meta = meta or get_meta(db)
    graph = graph or mangaluru_graph()
    road_status = {c.segment_id: c.kind for c in db.scalars(
        select(RoadClosure).where(RoadClosure.city_id == meta.city_id, RoadClosure.lifted_at.is_(None)))}
    nodes = {n.id: NodeState(n.id, n.kind, n.name, n.zone, n.lat or n.y, n.lon or n.x, n.status, dict(n.attributes or {}))
             for n in db.scalars(select(InfrastructureNode).where(InfrastructureNode.dataset == DATASET_CURRENT,
                                                                  InfrastructureNode.kind != "intersection"))}
    edges = {e.id: EdgeState(e.id, e.kind, e.source_id, e.target_id, e.name, e.status, dict(e.attributes or {}))
             for e in db.scalars(select(InfrastructureEdge).where(InfrastructureEdge.dataset == DATASET_CURRENT))}
    return CitySnapshot(meta.state_version, meta.epoch, graph, road_status, nodes, edges)


def persist_changes(db: Session, changes: list[dict], *, city_id: int, epoch: int, plan_id=None) -> None:
    """Write the projected changes of an executed plan. Optimistic check: 'before' must still hold."""
    now = utcnow()
    for c in changes:
        if c["entity"] == "road":
            active = db.scalars(select(RoadClosure).where(RoadClosure.city_id == city_id, RoadClosure.segment_id == c["id"],
                                                          RoadClosure.lifted_at.is_(None)).with_for_update()).first()
            current = active.kind if active else "open"
            if current != c["before"]:
                raise RuntimeError(f"Concurrent modification of road {c['id']}: expected {c['before']}, found {current}")
            if active is not None:
                active.lifted_at, active.lifted_reason = now, f"Superseded by plan {plan_id}"
                db.flush()
            if c["after"] in ("closed", "caution"):
                db.add(RoadClosure(city_id=city_id, segment_id=c["id"], kind=c["after"], source="plan_execution",
                                   plan_id=plan_id, epoch=epoch, reason=f"Executed plan {plan_id}"))
            continue
        model = InfrastructureNode if c["entity"] == "node" else InfrastructureEdge
        row = db.get(model, c["id"], with_for_update=True)
        if row is None or row.status != c["before"]:
            raise RuntimeError(f"Concurrent modification of {c['id']}: expected {c['before']}, found {row and row.status}")
        row.status = c["after"]
    db.flush()


def clear_simulated_state(db: Session, reason: str, graph: RoadGraph | None = None) -> SimulationMeta:
    """Lift every active closure, restore the overlay baseline, take scenario sensors offline.

    Bumps epoch and state_version so earlier plans and approvals go stale. Deletes nothing.
    """
    graph = graph or mangaluru_graph()
    meta = db.scalars(select(SimulationMeta).where(SimulationMeta.id == 1).with_for_update()).first()
    if meta is None or meta.city_id is None:
        raise SimulationNotInitialized("City state is not initialised.")
    now = utcnow()
    db.execute(update(RoadClosure).where(RoadClosure.city_id == meta.city_id, RoadClosure.lifted_at.is_(None))
               .values(lifted_at=now, lifted_reason=reason))
    baseline_nodes, baseline_edges = build_overlay(graph)
    for nd in baseline_nodes:
        db.execute(update(InfrastructureNode).where(InfrastructureNode.id == nd.id).values(status=nd.status))
    for ed in baseline_edges:
        db.execute(update(InfrastructureEdge).where(InfrastructureEdge.id == ed.id).values(status=ed.status))
    db.execute(update(InfrastructureNode).where(InfrastructureNode.dataset == DATASET_CURRENT,
                                                InfrastructureNode.kind == "sensor").values(status="offline"))
    db.execute(update(SimulationRoute).where(SimulationRoute.active.is_(True)).values(active=False))
    meta.state_version += 1
    meta.epoch += 1
    meta.active_scenario = NO_SCENARIO
    meta.updated_at = now
    db.flush()
    return meta


def load_scenario(db: Session, key: str, now: datetime | None = None, graph: RoadGraph | None = None) -> SimulationMeta:
    if key not in SCENARIOS:
        raise ScenarioError(f"Unknown scenario '{key}'. Known: {', '.join(SCENARIOS)}")
    sc = SCENARIOS[key]
    graph = graph or mangaluru_graph()
    now = now or datetime.now(timezone.utc)
    meta = clear_simulated_state(db, f"Demo scenario {sc.code} loaded", graph)
    meta.active_scenario = key

    snapshot = load_snapshot(db, meta, graph)
    geo = incident_geo(snapshot, sc.lat, sc.lon, sc.category, list(sc.indicators))
    if not geo.segment_id or not geo.hazard_segment_ids:
        raise ScenarioError(f"Scenario {sc.code} location no longer snaps to the road graph; rebuild or update it.")
    for sid in sc.pre_closures:
        if sid not in graph.segments:
            raise ScenarioError(f"Scenario {sc.code} references segment {sid}, which is not in the current graph.")
        db.add(RoadClosure(city_id=meta.city_id, segment_id=sid, kind="closed", source="scenario", epoch=meta.epoch,
                           reason=f"SIMULATED pre-existing closure (demo scenario {sc.code})"))
    for zid, status in sc.zone_status.items():
        db.execute(update(InfrastructureNode).where(InfrastructureNode.id == zid).values(status=status))

    ordered = nearest_hazard_segments(graph, sc.lat, sc.lon, geo.hazard_segment_ids)
    for spec in sc.sensors:
        sid = f"SIM-{sc.code}-{'WL' if spec.kind == 'road' else 'VAULT'}{spec.key}"
        if spec.kind == "road":
            seg = graph.segments[ordered[min(spec.rank, len(ordered) - 1)]]
            lat, lon = seg.geometry[len(seg.geometry) // 2]
            attrs = {"monitors_segment": seg.id, "measurement": "water_level_cm", "unit": "cm", "threshold": 30,
                     "hazard": "road_flooding", "simulated": True, "scenario": key}
            name = f"Synthetic water-level sensor on {seg.label}"
        else:
            s, w, n, e = zone_bounds(graph, geo.zone_id)
            lat, lon = sc.lat + 0.0006, sc.lon + 0.0006
            attrs = {"monitors_zone": geo.zone_id, "measurement": "vault_water_cm", "unit": "cm", "threshold": 15,
                     "hazard": "vault_flooding", "simulated": True, "scenario": key}
            name = f"Synthetic vault sensor, {geo.zone_id} (simulated feeder zone)"
        row = db.get(InfrastructureNode, sid)
        if row is None:
            db.add(InfrastructureNode(id=sid, kind="sensor", name=name[:160], zone=geo.zone_id, x=lon, y=lat, lat=lat,
                                      lon=lon, dataset=DATASET_CURRENT, status=spec.status, attributes=attrs))
        else:
            row.name, row.zone, row.x, row.y, row.lat, row.lon = name[:160], geo.zone_id, lon, lat, lat, lon
            row.status, row.attributes = spec.status, attrs
        db.flush()
        for o in spec.observations:
            db.add(SensorObservation(sensor_id=sid, epoch=meta.epoch, measurement=attrs["measurement"], value=o.value,
                                     unit=attrs["unit"], observed_at=now - timedelta(minutes=o.minutes_ago),
                                     quality=o.quality, synthetic=True))
    db.flush()
    return meta


def scenario_report(sc: Scenario) -> dict:
    return {"category": sc.category, "description": sc.report_text, "lat": sc.lat, "lon": sc.lon,
            "indicators": list(sc.indicators), "location_description": sc.location_description}

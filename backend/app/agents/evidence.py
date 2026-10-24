"""Deterministic evidence retrieval and interpretation (part of Sentinel's work, no LLM).

Two evidence sources exist, and both are labelled:
  * Synthetic sensor observations. These exist only while an explicitly loaded demo scenario is
    active; GridPulse has no live sensor feed. Sensors near the reported location (road flooding)
    or in the report's simulated feeder zone (vault flooding) are consulted.
  * An authenticated operator's on-record confirmation of the hazard.
Nothing here invents readings: with no sensors and no confirmation, the hazard is UNCORROBORATED.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.geo.osm import haversine_m
from app.models import SensorObservation
from app.schemas.agents import EvidenceAssessment, HazardEvidence, IncidentGeo, Observation, OperatorConfirmation
from app.simulation.state import CitySnapshot

ACTIVE_SENSOR_STATUSES = ("online", "degraded")
CATEGORY_HAZARD = {
    "flood": "road_flooding",
    "fire": "fire",
    "road_obstruction": "road_obstruction",
    "power_outage": "power_outage",
    "medical_emergency": "medical_emergency",
    "accident": "collision",
    "rescue": "rescue_need",
    "police_assistance": "security_hazard",
    "violence_assault": "violence_report",
    "homicide": "suspected_homicide",
    "threat_weapon": "weapon_or_threat",
    "theft_robbery_burglary": "crime_report",
    "suspicious_activity": "suspicious_activity",
    "other": "unspecified",
}


def relevant_sensors(snapshot: CitySnapshot, geo: IncidentGeo | None) -> list[str]:
    if geo is None or not geo.in_coverage:
        return []
    hazard = set(geo.hazard_segment_ids)
    reach = max(geo.hazard_radius_m, 50.0) + 100.0
    ids = []
    for n in snapshot.nodes.values():
        if n.kind != "sensor" or n.status not in ACTIVE_SENSOR_STATUSES:
            continue
        a = n.attributes
        if a.get("monitors_zone") and a["monitors_zone"] == geo.zone_id:
            ids.append(n.id)
        elif a.get("monitors_segment") and (a["monitors_segment"] in hazard
                                            or haversine_m(geo.lat, geo.lon, n.lat, n.lon) <= reach):
            ids.append(n.id)
    return sorted(ids)


def fetch_observations(db: Session, snapshot: CitySnapshot, sensor_ids: list[str]) -> list[SensorObservation]:
    if not sensor_ids:
        return []
    return list(db.scalars(
        select(SensorObservation)
        .where(SensorObservation.epoch == snapshot.epoch, SensorObservation.sensor_id.in_(sensor_ids))
        .order_by(SensorObservation.sensor_id, SensorObservation.observed_at)
    ))


def assess_evidence(
    snapshot: CitySnapshot,
    geo: IncidentGeo | None,
    category: str,
    rows: list,
    now: datetime,
    fresh_minutes: int,
    confirmation: OperatorConfirmation | None = None,
    electrical_hazard_reported: bool = False,
) -> EvidenceAssessment:
    observations: list[Observation] = []
    for r in rows:
        sensor = snapshot.nodes[r.sensor_id]
        threshold = float(sensor.attributes["threshold"])
        age = (now - r.observed_at).total_seconds() / 60
        monitors = sensor.attributes.get("monitors_segment") or sensor.attributes.get("monitors_zone") or ""
        observations.append(Observation(
            id=r.id, sensor_id=r.sensor_id, sensor_name=sensor.name, monitors=monitors,
            measurement=r.measurement, value=r.value, unit=r.unit, threshold=threshold, observed_at=r.observed_at,
            age_minutes=round(age, 1), freshness="fresh" if age <= fresh_minutes else "stale",
            exceeds_threshold=r.value >= threshold, quality=r.quality, synthetic=r.synthetic,
        ))

    road_obs = [o for o in observations if snapshot.nodes[o.sensor_id].attributes.get("hazard") == "road_flooding"]
    zone_obs: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        if snapshot.nodes[o.sensor_id].attributes.get("monitors_zone"):
            zone_obs[snapshot.nodes[o.sensor_id].attributes["monitors_zone"]].append(o)

    warnings: list[str] = []
    location_hazard = None
    if geo is not None and geo.segment_id:
        hazard = CATEGORY_HAZARD.get(category, "unspecified")
        relevant = road_obs if hazard == "road_flooding" else []
        location_hazard = _classify(geo.segment_id, geo.segment_name or geo.segment_id, hazard, relevant)
        location_hazard.segment_ids = list(geo.hazard_segment_ids)
        if hazard != "road_flooding" and not relevant:
            location_hazard.explanation = (f"No sensor type in GridPulse observes {hazard.replace('_', ' ')}; only the "
                                           "citizen report supports it.")
        _apply_confirmation(location_hazard, confirmation)

    infra = []
    zones = set(zone_obs)
    if electrical_hazard_reported and geo is not None and geo.zone_id:
        zones.add(geo.zone_id)
    for zid in sorted(zones):
        if zid not in snapshot.nodes:
            continue
        h = _classify(zid, snapshot.nodes[zid].name, "vault_flooding" if zone_obs.get(zid) else "electrical_hazard",
                      zone_obs.get(zid, []))
        if electrical_hazard_reported and geo is not None and zid == geo.zone_id:
            _apply_confirmation(h, confirmation)
        infra.append(h)

    latest = _latest_per_sensor(observations)
    for o in latest.values():
        if o.freshness == "stale":
            warnings.append(f"{o.sensor_name} ({o.sensor_id}) last reported {o.age_minutes:.0f} min ago; "
                            f"readings older than {fresh_minutes} min are treated as stale.")
        if o.quality != "ok":
            warnings.append(f"{o.sensor_name} ({o.sensor_id}) reports data quality '{o.quality}'.")
    if geo is None or not geo.in_coverage:
        warnings.append("The reported location is outside the mapped Mangaluru coverage area.")
    elif geo.segment_id is None:
        warnings.append("The reported location is not near any mapped road; no road evidence retrieved.")
    if observations:
        warnings.append("All sensor observations are SYNTHETIC, generated by a loaded demo scenario.")

    return EvidenceAssessment(
        location_id=geo.segment_id if geo else None, geo=geo, operator_confirmation=confirmation,
        location_hazard=location_hazard, infrastructure_hazards=infra, observations=observations,
        warnings=warnings, fresh_window_minutes=fresh_minutes, assessed_at=now,
    )


def _apply_confirmation(h: HazardEvidence, confirmation: OperatorConfirmation | None) -> None:
    h.sensor_status = h.status
    if confirmation is None or h.status == "CORROBORATED":
        return
    note = f' ("{confirmation.note}")' if confirmation.note else ""
    h.explanation = (f"Confirmed on record by operator {confirmation.operator} at "
                     f"{confirmation.confirmed_at:%Y-%m-%d %H:%M} UTC{note}. Sensor evidence alone: {h.status}. "
                     + h.explanation)
    h.status = "OPERATOR_CONFIRMED"


def _latest_per_sensor(obs: list[Observation]) -> dict[str, Observation]:
    latest: dict[str, Observation] = {}
    for o in obs:
        if o.sensor_id not in latest or o.observed_at > latest[o.sensor_id].observed_at:
            latest[o.sensor_id] = o
    return latest


def _classify(target_id: str, target_name: str, hazard: str, obs: list[Observation]) -> HazardEvidence:
    latest = _latest_per_sensor(obs)
    sensors = sorted(latest)
    ids = [o.id for o in obs]
    fresh_high = [o for o in latest.values() if o.freshness == "fresh" and o.exceeds_threshold]
    fresh_low = [o for o in latest.values() if o.freshness == "fresh" and not o.exceeds_threshold]
    stale_high = [o for o in latest.values() if o.freshness == "stale" and o.exceeds_threshold]

    def fmt(items: list[Observation]) -> str:
        return ", ".join(f"{o.sensor_id} {o.value:g}{o.unit} ({o.age_minutes:.0f} min ago, threshold {o.threshold:g})"
                         for o in items)

    if not latest:
        status, why = "UNCORROBORATED", "No sensor observes this location; only the citizen report supports it."
    elif fresh_high and not fresh_low:
        status, why = "CORROBORATED", f"Fresh synthetic readings exceed threshold: {fmt(fresh_high)}."
    elif fresh_high and fresh_low:
        status, why = "CONTRADICTORY", f"Fresh sensors disagree: high {fmt(fresh_high)}; normal {fmt(fresh_low)}."
    elif fresh_low and stale_high:
        status, why = "CONTRADICTORY", f"Only a stale reading is high ({fmt(stale_high)}) while a fresh one is normal ({fmt(fresh_low)})."
    elif fresh_low:
        status, why = "NOT_INDICATED", f"Fresh readings are below threshold: {fmt(fresh_low)}."
    elif stale_high:
        status, why = "STALE", f"The only high readings are stale: {fmt(stale_high)}."
    else:
        status, why = "STALE", "All readings are stale and below threshold."
    return HazardEvidence(target_id=target_id, target_name=target_name, hazard=hazard, status=status,
                          sensors=sensors, observation_ids=ids, explanation=why)

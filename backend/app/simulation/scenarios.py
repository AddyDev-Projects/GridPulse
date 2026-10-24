"""Explicit, opt-in DEMO scenarios on the real Mangaluru road graph.

Nothing here runs at startup. A scenario is loaded only by `python -m app.cli demo load <key>` or by
an authenticated operator in the console. Each one:
  * uses an ILLUSTRATIVE location on the real map (no real event is implied),
  * creates SYNTHETIC sensors and observations, clearly flagged,
  * may add SIMULATED pre-existing road closures or feeder-zone outages.
Scenario locations were chosen by searching the graph (see docs) and are re-checked when loaded,
so a stale graph cannot silently produce a scenario that no longer demonstrates what it claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geo.osm import haversine_m


@dataclass(frozen=True)
class ObservationSpec:
    minutes_ago: float
    value: float
    quality: str = "ok"


@dataclass(frozen=True)
class SensorSpec:
    key: str  # suffix of the sensor id
    kind: str  # "road" (water level on the nearest hazard segment) | "vault" (feeder-zone vault)
    rank: int = 0  # for road sensors: the n-th nearest hazard segment
    observations: tuple[ObservationSpec, ...] = ()
    status: str = "online"


@dataclass(frozen=True)
class Scenario:
    key: str
    code: str
    title: str
    summary: str
    expected_outcome: str
    lat: float
    lon: float
    category: str
    report_text: str
    indicators: tuple[str, ...]
    location_description: str
    sensors: tuple[SensorSpec, ...] = ()
    pre_closures: tuple[str, ...] = ()  # real segment ids closed by the scenario (simulated)
    zone_status: dict[str, str] = field(default_factory=dict)
    expects: str = "conflict"  # conflict | verification_first | no_route | approvable


_FLOOD_SENSORS = (
    SensorSpec("1", "road", 0, (ObservationSpec(22, 18), ObservationSpec(11, 34), ObservationSpec(2, 47))),
    SensorSpec("2", "road", 1, (ObservationSpec(16, 22), ObservationSpec(6, 39), ObservationSpec(1, 52))),
    SensorSpec("V", "vault", 0, (ObservationSpec(13, 9), ObservationSpec(3, 17))),
)

# Scenario A location: found by searching the graph for a point whose emergency-access route crosses
# a real OSM traffic signal in the same simulated feeder zone, with a feasible detour.
_A = (12.899209, 74.856095)

SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario(
            key="flood_conflict", code="A", title="Flood with a resolvable conflict",
            summary="Simulated flooding at an illustrative location in central Mangaluru. Synthetic sensors "
                    "corroborate it, including water in the simulated feeder-zone vault.",
            expected_outcome="Mobility routes responders across a real signalised junction; Utility isolates the "
                             "simulated feeder zone powering that signal. The validator blocks the plan and Mobility "
                             "reroutes on the real graph.",
            lat=_A[0], lon=_A[1], category="flood",
            report_text="Water is rising fast on this road, it's above the kerb and a car is stuck. Water is also "
                        "going into the electrical box near the junction.",
            indicators=("water_rising", "vehicles_stranded"), location_description="Illustrative location (simulation)",
            sensors=_FLOOD_SENSORS, expects="conflict",
        ),
        Scenario(
            key="stale_evidence", code="B", title="Conflicting or stale sensor evidence",
            summary="A flood report where the only high synthetic reading is hours old and a fresh sensor reads dry.",
            expected_outcome="Evidence is CONTRADICTORY, high-impact actions are blocked, and the plan is revised to "
                             "verification-first actions. An operator can confirm the hazard on record instead.",
            lat=_A[0], lon=_A[1], category="flood",
            report_text="Someone in our area group said this road was flooded earlier today, I haven't seen it myself.",
            indicators=(), location_description="Illustrative location (simulation)",
            sensors=(
                SensorSpec("1", "road", 0, (ObservationSpec(340, 41, "degraded"),), status="degraded"),
                SensorSpec("2", "road", 1, (ObservationSpec(14, 3), ObservationSpec(3, 2))),
                SensorSpec("V", "vault", 0, (ObservationSpec(5, 3),)),
            ),
            expects="verification_first",
        ),
        Scenario(
            key="no_safe_plan", code="C", title="No safe route",
            summary="Simulated flooding in an area that, within GridPulse's mapped coverage, is reached only via "
                    "Tannirbaavi Beach Road; the scenario also simulates a pre-existing closure of that road.",
            expected_outcome="No responder base can reach the hazard perimeter. Mobility reports no route and the "
                             "coordinator escalates without changing the city. (In reality this area also connects "
                             "northwards outside the mapped bounding box; the dead end is a coverage limitation.)",
            lat=12.858388, lon=74.825563, category="flood",
            report_text="The road here is completely under water and the only way in is blocked too.",
            indicators=("water_rising",), location_description="Illustrative location (simulation)",
            sensors=_FLOOD_SENSORS[:2], pre_closures=("w164559538s0",), expects="no_route",
        ),
        Scenario(
            key="approval_rejection", code="D", title="Approval rejection",
            summary="Scenario A's synthetic data. Use it to show that rejecting a valid plan changes nothing.",
            expected_outcome="The operator rejects the validated plan; no simulated road or utility state changes.",
            lat=_A[0], lon=_A[1], category="flood",
            report_text="Flooding on this road, water rising quickly and reaching the electrical box.",
            indicators=("water_rising",), location_description="Illustrative location (simulation)",
            sensors=_FLOOD_SENSORS, expects="conflict",
        ),
    ]
}


def nearest_hazard_segments(graph, lat: float, lon: float, hazard_segment_ids: list[str]) -> list[str]:
    def dist(sid: str) -> float:
        g = graph.segments[sid].geometry
        mid = g[len(g) // 2]
        return haversine_m(lat, lon, mid[0], mid[1])
    return sorted(hazard_segment_ids, key=lambda s: (dist(s), s))

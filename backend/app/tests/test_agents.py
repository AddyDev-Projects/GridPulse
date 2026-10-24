"""Agents, validator and revision on the real Mangaluru graph (pure functions, no database).

Scenario contexts are rebuilt here the same way repository.load_scenario builds them, using
synthetic observations; these are test fixtures, never loaded by the application.
"""

from datetime import datetime, timezone

import pytest

from app.agents import grid, mobility, sentinel, traffic
from app.agents.evidence import assess_evidence, relevant_sensors
from app.agents.geo_context import incident_geo
from app.orchestration.revision import RevisionFailure, RevisionOutcome, diff_actions, make_action, resequence, revise
from app.schemas.agents import OperatorConfirmation
from app.simulation.baseline import baseline_snapshot
from app.simulation.scenarios import SCENARIOS, nearest_hazard_segments
from app.simulation.state import ActionSpec, NodeState, apply_actions
from app.tests.conftest import fake_rows
from app.validation.validator import validate_plan

NOW = datetime.now(timezone.utc)


def context(key: str, *, confirm: bool = False, mutate=None):
    sc = SCENARIOS[key]
    snap = baseline_snapshot()
    for sid in sc.pre_closures:
        snap.road_status[sid] = "closed"
    for zid, st in sc.zone_status.items():
        snap.nodes[zid].status = st
    if mutate:
        mutate(snap)
    snap.recompute_derived()
    geo = incident_geo(snap, sc.lat, sc.lon, sc.category, list(sc.indicators))
    ordered = nearest_hazard_segments(snap.graph, sc.lat, sc.lon, geo.hazard_segment_ids)
    specs = []
    for s in sc.sensors:
        sid = f"SIM-{sc.code}-{'WL' if s.kind == 'road' else 'VAULT'}{s.key}"
        if s.kind == "road":
            seg = snap.graph.segments[ordered[min(s.rank, len(ordered) - 1)]]
            lat, lon = seg.geometry[0]
            attrs = {"monitors_segment": seg.id, "threshold": 30, "hazard": "road_flooding"}
        else:
            lat, lon = sc.lat, sc.lon
            attrs = {"monitors_zone": geo.zone_id, "threshold": 15, "hazard": "vault_flooding"}
        snap.nodes[sid] = NodeState(sid, "sensor", sid, geo.zone_id, lat, lon, s.status, attrs)
        specs += [(sid, o.minutes_ago, o.value, o.quality) for o in s.observations]
    conf = OperatorConfirmation(operator="ops.test", confirmed_at=NOW, note="Called back") if confirm else None
    ev = assess_evidence(snap, geo, sc.category, fake_rows([x for x in specs if x[0] in relevant_sensors(snap, geo)], NOW),
                         NOW, 30, conf, electrical_hazard_reported=False)
    a = sentinel.rule_based_triage(sc.report_text, sc.category, list(sc.indicators), geo)
    return snap, geo, ev, a, list(sc.indicators)


def plan_v1(snap, geo, ev, a, indicators):
    mp = mobility.propose(snap, a, ev, geo, indicators)
    gp = grid.propose(snap, ev)
    return resequence([make_action(i, p) for i, p in enumerate(mp.actions + gp.actions, 1)]), mp


# ---- geography & evidence --------------------------------------------------------------------
def test_incident_geo_snaps_to_real_road_and_models_hazard_area():
    snap, geo, *_ = context("flood_conflict")
    assert geo.in_coverage and geo.segment_id in snap.graph.segments and geo.snap_distance_m < 100
    assert geo.hazard_radius_m == pytest.approx(195.0)  # 150 m flood radius x 1.3 (water rising)
    assert len(geo.hazard_segment_ids) >= 3 and geo.zone_id == "SIM-FZ-2B"


def test_report_outside_coverage_has_no_road_context():
    snap = baseline_snapshot()
    geo = incident_geo(snap, 12.0, 77.5, "flood", [])
    assert not geo.in_coverage and geo.segment_id is None and geo.hazard_segment_ids == []


def test_evidence_statuses_from_synthetic_sensors():
    _, _, ev_a, *_ = context("flood_conflict")
    assert ev_a.location_hazard.status == "CORROBORATED"
    assert ev_a.hazard_for("SIM-FZ-2B").status == "CORROBORATED"
    assert all(o.synthetic for o in ev_a.observations)
    _, _, ev_b, *_ = context("stale_evidence")
    assert ev_b.location_hazard.status == "CONTRADICTORY"
    assert any("stale" in w for w in ev_b.warnings)


def test_no_sensors_means_uncorroborated_never_invented():
    snap = baseline_snapshot()
    sc = SCENARIOS["flood_conflict"]
    geo = incident_geo(snap, sc.lat, sc.lon, "flood", [])
    ev = assess_evidence(snap, geo, "flood", [], NOW, 30)
    assert ev.location_hazard.status == "UNCORROBORATED" and ev.observations == []


def test_operator_confirmation_is_recorded_evidence():
    _, _, ev, *_ = context("stale_evidence", confirm=True)
    h = ev.location_hazard
    assert h.status == "OPERATOR_CONFIRMED" and h.sensor_status == "CONTRADICTORY"
    assert "ops.test" in h.explanation


# ---- Sentinel ----------------------------------------------------------------------------------
def test_sentinel_rules_use_reporter_category_and_map_location():
    snap, geo, *_ = context("flood_conflict")
    a = sentinel.rule_based_triage("Smoke everywhere, I think there's a fire.", "fire", ["people_trapped"], geo)
    assert a.category == "fire" and a.urgency == "critical"
    assert a.location_id == geo.segment_id and a.location_name == geo.segment_name
    assert any("unverified" in i for i in a.inferences)


def test_resource_recommendations_match_reported_hazards_without_over_dispatching():
    snap = baseline_snapshot()
    sc = SCENARIOS["flood_conflict"]
    geo = incident_geo(snap, sc.lat, sc.lon, "fire", ["people_trapped"])
    fire = sentinel.rule_based_triage("A building is on fire and people are trapped upstairs.", "fire",
                                     ["people_trapped"], geo)
    types = {r.responder_type for r in fire.responder_recommendations}
    assert types == {"fire_rescue", "ambulance"}
    assert fire.medical_need == "indicated"
    assert any("unverified" in r.rationale.lower() for r in fire.responder_recommendations)

    police_geo = incident_geo(snap, sc.lat, sc.lon, "homicide", [])
    police = sentinel.rule_based_triage("A possible homicide has been reported; the allegation is unverified.", "homicide", [], police_geo)
    assert {r.responder_type for r in police.responder_recommendations} == {"police"}
    assert police.responder_recommendations[0].status == "verification_required"
    assert police.medical_need == "not_indicated"


def test_analyse_traffic_is_invoked_with_explicit_degraded_data_mode():
    snap, geo, ev, a, ind = context("flood_conflict")
    mp = mobility.propose(snap, a, ev, geo, ind)
    tp = traffic.analyse(snap, a, ev, geo, mp)
    assert tp.data_source == "estimated"
    assert tp.live_traffic == "unavailable"
    assert tp.route_assessments
    assert any("live traffic" in text.lower() for text in tp.uncertainty)


def test_injury_collision_ranks_reachable_hospitals_without_claiming_capacity():
    snap = baseline_snapshot()
    sc = SCENARIOS["flood_conflict"]
    geo = incident_geo(snap, sc.lat, sc.lon, "accident", ["multiple_injured"])
    ev = assess_evidence(snap, geo, "accident", [], NOW, 30)
    a = sentinel.rule_based_triage("Several people are injured after a road collision.", "accident",
                                  ["multiple_injured"], geo)
    mp = mobility.propose(snap, a, ev, geo, ["multiple_injured"])
    assert a.medical_need == "indicated" and mp.facility_recommendations
    assert all(x["capability_status"] == "unverified" and x["availability_status"] == "unknown"
               for x in mp.facility_recommendations)


class _Hallucinating:
    name, model = "test", "fake"

    def complete(self, system, user, schema):
        return schema(category="flood", location_id="w999999999s0", summary="x", urgency="critical", confidence=0.9)


def test_sentinel_discards_model_location():
    snap, geo, *_ = context("flood_conflict")
    out = sentinel.triage("Water on the road.", "flood", [], geo, _Hallucinating())
    assert out.assessment.location_id == geo.segment_id
    assert any("discarded" in m for m in out.assessment.missing_information)


# ---- Mobility & Grid ---------------------------------------------------------------------------
def test_mobility_routes_from_real_osm_facilities_and_avoids_hazard():
    snap, geo, ev, a, ind = context("flood_conflict")
    mp = mobility.propose(snap, a, ev, geo, ind)
    close = next(x for x in mp.actions if x.action_type == "close_roads")
    assert set(close.params["segment_ids"]) == set(geo.hazard_segment_ids)
    access = next(r for r in mp.routes if r.purpose == "emergency_access")
    assert "Fire Station" in access.origin_label or "Police" in access.origin_label
    assert not set(access.route.edge_path) & set(geo.hazard_segment_ids)
    assert snap.graph.is_continuous(access.route.node_path, access.route.edge_path) == []


def test_mobility_reports_no_route_when_area_is_cut_off():
    snap, geo, ev, a, ind = context("no_safe_plan")
    mp = mobility.propose(snap, a, ev, geo, ind)
    assert "emergency_access" in mp.failed and not mp.routes
    snap2, geo2, ev2, a2, ind2 = context("no_safe_plan", mutate=lambda s: None)
    snap2.road_status.clear()  # without the simulated pre-existing closure the area is reachable
    assert mobility.propose(snap2, a2, ev2, geo2, ind2).routes


def test_grid_isolates_only_on_supported_evidence():
    snap, geo, ev, *_ = context("flood_conflict")
    gp = grid.propose(snap, ev)
    assert [x.target_id for x in gp.actions] == ["SIM-FZ-2B"]
    assert any(c["id"].startswith("SIG-") for c in gp.consequences)
    _, _, ev_b, *_ = context("stale_evidence")
    assert grid.propose(snap, ev_b).actions == []


# ---- Validator ---------------------------------------------------------------------------------
def test_validator_detects_real_junction_dependency_conflict():
    snap, geo, ev, a, ind = context("flood_conflict")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    result = validate_plan(v1, snap, ev)
    blocking = result.blocking()
    assert blocking and all(f.rule_id == "R2_INFRA_DEPENDENCY" for f in blocking)
    chain = blocking[0].evidence["dependency_chain"]
    assert chain[0] == "SIM-FZ-2B" and chain[1].startswith("SIG-n") and chain[2] in snap.graph.signals


@pytest.mark.parametrize("action,needle", [
    (ActionSpec(1, "open_floodgates", "x", {}), "Unknown action"),
    (ActionSpec(1, "close_roads", "w1s0", {"segment_ids": ["w1s0"]}), "Invalid target"),
    (ActionSpec(1, "isolate_substation", "SIG-n1952685820", {}), "Invalid target"),
    (ActionSpec(1, "issue_public_advisory", "x", {"message": "hello there", "extra": 1}), "Malformed"),
])
def test_validator_rejects_invalid_actions(action, needle):
    result = validate_plan([action], baseline_snapshot(), None)
    assert any(f.rule_id == "R3_ACTION_VALIDITY" and needle in f.title for f in result.blocking())


def test_validator_rejects_routes_through_closed_or_wrong_way_segments():
    snap, geo, ev, a, ind = context("flood_conflict")
    _, mp = plan_v1(snap, geo, ev, a, ind)
    route = next(x for x in mp.actions if x.action_type == "designate_response_route")
    act = make_action(1, route)
    snap.road_status[route.params["edge_path"][2]] = "closed"
    assert any("closed in the current" in f.title for f in validate_plan([act], snap, None).blocking())
    one = next(s for s in snap.graph.segments.values() if s.oneway and s.u != s.v)
    wrong = ActionSpec(1, "designate_response_route", one.v, {**route.params, "origin": one.v, "destination": one.u,
                                                             "node_path": [one.v, one.u], "edge_path": [one.id]})
    assert any("not a valid path" in f.title for f in validate_plan([wrong], baseline_snapshot(), None).blocking())


def test_validator_blocks_high_impact_on_unsupported_evidence_and_stale_state():
    snap, geo, ev, a, ind = context("stale_evidence")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    assert {f.rule_id for f in validate_plan(v1, snap, ev).blocking()} == {"R6_EVIDENCE"}
    stale = validate_plan([ActionSpec(1, "set_roads_caution", geo.segment_id, {"segment_ids": [geo.segment_id]})],
                          baseline_snapshot(state_version=5), None, expected_state_version=4)
    assert [f.rule_id for f in stale.blocking()] == ["R5_STALE_STATE"]


def test_operator_confirmation_unblocks_evidence_rule():
    snap, geo, ev, a, ind = context("stale_evidence", confirm=True)
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    assert not [f for f in validate_plan(v1, snap, ev).blocking() if f.rule_id == "R6_EVIDENCE"]


# ---- Revision ----------------------------------------------------------------------------------
def test_revision_reroutes_on_the_real_graph_and_revalidates():
    snap, geo, ev, a, ind = context("flood_conflict")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    out = revise(v1, validate_plan(v1, snap, ev), snap, a, ev, geo, ind)
    assert isinstance(out, RevisionOutcome) and out.strategy == "mobility_reroute"
    before = next(x for x in v1 if x.params.get("purpose") == "emergency_access")
    after = next(x for x in out.actions if x.params.get("purpose") == "emergency_access")
    dark = {e for e in ("n1952685820",)}
    assert dark & set(before.params["node_path"]) and not dark & set(after.params["node_path"])
    assert any(x.action_type == "isolate_substation" for x in out.actions), "hazard isolation is kept"
    assert validate_plan(out.actions, snap, ev).passed
    d = diff_actions(v1, out.actions)
    assert d["routes_before"]["emergency_access"]["edge_path"] != d["routes_after"]["emergency_access"]["edge_path"]


def test_revision_uses_simulated_backup_power_when_no_detour_exists():
    snap, geo, ev, a, ind = context("flood_conflict")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    result = validate_plan(v1, snap, ev)
    original = mobility.propose
    try:
        mobility.propose = lambda *args, **kw: mobility.MobilityProposal([], [], ["emergency_access"], None, [], [], [])
        out = revise(v1, result, snap, a, ev, geo, ind)
    finally:
        mobility.propose = original
    assert isinstance(out, RevisionOutcome) and out.strategy == "grid_backup_power"
    transfer = [x for x in out.actions if x.action_type == "transfer_signal_power"]
    isolate = next(x for x in out.actions if x.action_type == "isolate_substation")
    assert transfer and all(t.seq < isolate.seq for t in transfer)
    projected, _ = apply_actions(snap, out.actions)
    assert all(projected.nodes[t.target_id].status == "operational" for t in transfer)


def test_verification_first_revision_for_contradictory_evidence():
    snap, geo, ev, a, ind = context("stale_evidence")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    out = revise(v1, validate_plan(v1, snap, ev), snap, a, ev, geo, ind)
    types = {x.action_type for x in out.actions}
    assert types == {"set_roads_caution", "dispatch_field_verification", "issue_public_advisory"}
    assert validate_plan(out.actions, snap, ev).passed


def test_revision_failure_when_nothing_resolves_the_conflict():
    snap, geo, ev, a, ind = context("flood_conflict")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    result = validate_plan(v1, snap, ev)
    for e in snap.edges.values():  # remove every backup feed
        if e.attributes.get("role") == "backup":
            snap.nodes[e.source].status = "maintenance"
    original = mobility.propose
    try:
        mobility.propose = lambda *args, **kw: mobility.MobilityProposal([], [], ["emergency_access"], None, [], [], [])
        out = revise(v1, result, snap, a, ev, geo, ind)
    finally:
        mobility.propose = original
    assert isinstance(out, RevisionFailure) and not any(alt["viable"] for alt in out.alternatives)

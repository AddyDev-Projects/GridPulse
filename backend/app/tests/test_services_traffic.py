"""Service selection, responder routing, AnalyseTraffic and the R3/R8 rules (pure functions, no database).

Locations are scenario A's real coordinates on the Mangaluru graph; nothing here is loaded by the app.
"""

from datetime import datetime, timezone

from app.agents import grid, mobility, sentinel, traffic
from app.agents.evidence import assess_evidence
from app.agents.geo_context import incident_geo
from app.agents.services import select_services
from app.orchestration.revision import make_action, resequence, revise
from app.schemas.agents import OperatorConfirmation
from app.simulation.baseline import baseline_snapshot
from app.simulation.scenarios import SCENARIOS
from app.simulation.state import ActionSpec
from app.tests.test_agents import context, plan_v1
from app.validation.validator import validate_plan

NOW = datetime.now(timezone.utc)
A = SCENARIOS["flood_conflict"]
CONFIRMED = OperatorConfirmation(operator="ops.test", confirmed_at=NOW, note="Called back")


def incident(category: str, indicators: list[str], text: str, *, snap=None, confirm: bool = True):
    snap = snap or baseline_snapshot()
    geo = incident_geo(snap, A.lat, A.lon, category, indicators)
    ev = assess_evidence(snap, geo, category, [], NOW, 30, CONFIRMED if confirm else None)
    a = sentinel.rule_based_triage(text, category, indicators, geo)
    services = select_services(category, indicators, a)
    return snap, geo, ev, a, services


def statuses(services) -> dict[str, str]:
    return {d.service: d.status for d in services.decisions}


def actions_of(snap, geo, ev, a, indicators, services) -> tuple[list[ActionSpec], mobility.MobilityProposal]:
    mp = mobility.propose(snap, a, ev, geo, indicators, services=services)
    return resequence([make_action(i, p) for i, p in enumerate(mp.actions, 1)]), mp


# ---- service selection -------------------------------------------------------------------------
def test_police_only_incident_gets_a_police_route_and_no_fire_response():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Someone broke into the shop and stole things.")
    assert statuses(services) == {"fire_rescue": "not_justified", "ambulance": "not_justified", "police": "required"}
    acts, mp = actions_of(snap, geo, ev, a, [], services)
    assert [r.purpose for r in mp.routes] == ["police_response"]
    assert mp.routes[0].service == "police" and "Police Station" in mp.routes[0].origin_label
    assert {p.id for p in snap.graph.pois if p.kind == "police"} >= {
        p.id for p in snap.graph.pois if p.label == mp.routes[0].origin_label}
    assert not any(x.action_type == "close_roads" for x in acts)
    assert all(f["kind"] == "police" and f["availability_status"] == "unknown" for f in mp.facility_recommendations)
    assert "emergency_access" not in mp.failed and not any(r.purpose == "emergency_access" for r in mp.routes)


def test_medical_only_incident_plans_a_hospital_route_and_no_fire_response():
    snap, geo, ev, a, services = incident("medical_emergency", ["person_unconscious"], "A man collapsed and is unconscious.")
    assert statuses(services) == {"fire_rescue": "not_justified", "ambulance": "required", "police": "not_justified"}
    _, mp = actions_of(snap, geo, ev, a, ["person_unconscious"], services)
    assert [r.purpose for r in mp.routes] == ["medical_transfer"]
    assert mp.routes[0].service == "ambulance"
    assert mp.facility_recommendations and all(f["kind"] == "hospital" for f in mp.facility_recommendations)


def test_multi_service_incident_gets_each_justified_route_once():
    ind = ["people_trapped", "injuries"]
    snap, geo, ev, a, services = incident("accident", ind, "Two cars collided, a driver is trapped and people are hurt.")
    st = statuses(services)
    assert st == {"fire_rescue": "required", "ambulance": "required", "police": "required"}
    assert {d.service: d.basis for d in services.decisions} == {"fire_rescue": "reported", "ambulance": "reported", "police": "rule"}
    _, mp = actions_of(snap, geo, ev, a, ind, services)
    assert sorted(r.purpose for r in mp.routes) == ["emergency_access", "medical_transfer", "police_response"]
    assert len({r.purpose for r in mp.routes}) == len(mp.routes)


def test_fire_with_weapon_requires_fire_and_police():
    ind = ["fire_spreading", "weapon_present"]
    snap, geo, ev, a, services = incident("fire", ind, "A shop is on fire and a man with a knife is outside.")
    assert statuses(services)["police"] == "required" and statuses(services)["fire_rescue"] == "required"
    _, mp = actions_of(snap, geo, ev, a, ind, services)
    assert {r.purpose for r in mp.routes} == {"emergency_access", "police_response"}
    fire = next(r for r in mp.routes if r.purpose == "emergency_access")
    assert fire.origin_label.startswith("Fire Station")  # never a police station standing in for fire


def test_text_only_mentions_stay_optional_and_get_no_route():
    snap, geo, ev, a, services = incident("other", [], "There is a suspicious man with a knife near the bus stop.")
    assert statuses(services)["police"] == "conditional"
    assert next(d for d in services.decisions if d.service == "police").basis == "inference"
    assert services.required() == []
    _, mp = actions_of(snap, geo, ev, a, [], services)
    assert mp.routes == [] and mp.failed == []


def test_police_report_in_a_flooded_zone_does_not_inherit_utility_actions():
    snap, geo, ev, *_ = context("flood_conflict")
    assert ev.infrastructure_hazards  # the zone really is flagged for the flood report
    police_ev = assess_evidence(snap, geo, "theft_robbery_burglary", [], NOW, 30)
    assert police_ev.infrastructure_hazards == [] and grid.propose(snap, police_ev).actions == []


# ---- AnalyseTraffic -----------------------------------------------------------------------------
def test_route_without_disruption_is_clear_and_bound_to_its_plan_version():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, _ = actions_of(snap, geo, ev, a, [], services)
    tp = traffic.analyse_plan(snap, acts, ev, plan_version=3)
    (ra,) = tp.route_assessments
    assert ra["status"] == "clear" and ra["closed_segments"] == [] and ra["caution_segments"] == []
    assert tp.plan_version == 3 and tp.state_version == snap.state_version and tp.live_traffic == "unavailable"
    assert "free-flow" in ra["travel_time_basis"]


def test_routes_avoid_currently_closed_roads():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, mp = actions_of(snap, geo, ev, a, [], services)
    middle = mp.routes[0].route.edge_path[len(mp.routes[0].route.edge_path) // 2]
    snap2 = baseline_snapshot()
    snap2.road_status[middle] = "closed"
    snap2.recompute_derived()
    snap2, geo2, ev2, a2, services2 = incident("theft_robbery_burglary", [], "Shop break-in.", snap=snap2)
    _, mp2 = actions_of(snap2, geo2, ev2, a2, [], services2)
    assert mp2.routes and middle not in mp2.routes[0].route.edge_path


def test_alternative_route_is_meaningfully_different():
    snap, geo, ev, a, ind = context("flood_conflict", confirm=True)
    acts, _ = plan_v1(snap, geo, ev, a, ind)
    tp = traffic.analyse_plan(snap, acts, ev, plan_version=1)
    alt = tp.alternative_routes[0]
    original = next(x for x in acts if x.action_type == "designate_response_route")
    assert alt["edge_path"] != original.params["edge_path"] and alt["overlap_pct"] <= 80
    assert not set(alt["edge_path"]) & set(geo.hazard_segment_ids)


def test_no_alternative_is_reported_not_invented():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, mp = actions_of(snap, geo, ev, a, [], services)
    keep = set(mp.routes[0].route.edge_path)
    for sid in snap.graph.segments:
        if sid not in keep:
            snap.road_status[sid] = "closed"
    tp = traffic.analyse_plan(snap, acts, ev, plan_version=1)
    (ra,) = tp.route_assessments
    assert ra["alternative"] is None and "No meaningfully different route" in ra["alternative_note"]
    assert tp.alternative_routes == []
    result = validate_plan(acts, snap, ev, traffic=tp.as_dict(), plan_version=1)
    assert any(f.rule_id == "R8_TRAFFIC_DATA" and f.severity == "info" and "No alternative" in f.title for f in result.findings)


def test_route_invalidated_by_its_own_plan_closure_is_blocked():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, mp = actions_of(snap, geo, ev, a, [], services)
    on_route = mp.routes[0].route.edge_path[2]
    acts = resequence(acts + [ActionSpec(99, "close_roads", on_route, {"segment_ids": [on_route]}, "mobility", "high", "test")])
    tp = traffic.analyse_plan(snap, acts, ev, plan_version=1)
    assert tp.route_assessments[0]["status"] == "blocked" and tp.route_assessments[0]["closed_by_this_plan"] == [on_route]
    result = validate_plan(acts, snap, ev, traffic=tp.as_dict(), plan_version=1)
    assert any(f.rule_id == "R1_ROAD_SAFETY" and f.severity == "blocking" and "closed by this same plan" in f.title
               for f in result.findings)
    assert not result.passed


def test_failed_or_stale_traffic_analysis_warns_without_blocking():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, _ = actions_of(snap, geo, ev, a, [], services)
    failed = validate_plan(acts, snap, ev, traffic=traffic.failed_context("TimeoutError", 1, snap.state_version), plan_version=1)
    assert failed.passed
    assert any(f.rule_id == "R8_TRAFFIC_DATA" and f.title == "Traffic analysis unavailable" for f in failed.findings)
    old = traffic.analyse_plan(snap, acts, ev, plan_version=1).as_dict()
    old["state_version"] = snap.state_version - 1
    stale = validate_plan(acts, snap, ev, traffic=old, plan_version=1)
    assert stale.passed and any(f.title == "Traffic analysis is out of date" for f in stale.findings)
    other_version = validate_plan(acts, snap, ev, traffic=traffic.analyse_plan(snap, acts, ev, plan_version=1).as_dict(),
                                  plan_version=2)
    assert any(f.title == "Traffic analysis is out of date" for f in other_version.findings)
    current = validate_plan(acts, snap, ev, traffic=traffic.analyse_plan(snap, acts, ev, plan_version=1).as_dict(), plan_version=1)
    assert not any(f.title == "Traffic analysis is out of date" for f in current.findings)


def test_traffic_reroutes_around_caution_and_changes_the_plan():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, mp = actions_of(snap, geo, ev, a, [], services)
    route = mp.routes[0].route.edge_path
    for sid in route[1:-1]:  # try interior segments until one has a short detour
        trial = snap.clone()
        trial.road_status[sid] = "caution"
        adjusted, changes = traffic.adjust_plan(trial, acts, ev, "theft_robbery_burglary")
        if changes and changes[0]["type"] == "reroute_around_caution":
            new = next(x for x in adjusted if x.action_type == "designate_response_route")
            assert new.proposed_by == "traffic" and sid not in new.params["edge_path"]
            assert new.params["purpose"] == "police_response" and new.params["service"] == "police"
            assert new.params["origin"] == route_origin(acts) and trial.graph.is_continuous(new.params["node_path"], new.params["edge_path"]) == []
            assert validate_plan(adjusted, trial, ev).passed
            return
    raise AssertionError("no caution segment on the route had a detour within the limit")


def route_origin(acts):
    return next(x for x in acts if x.action_type == "designate_response_route").params["origin"]


def test_traffic_adds_a_diversion_notice_for_closures_but_not_for_sensitive_reports():
    snap, geo, ev, a, ind = context("flood_conflict", confirm=True)
    acts, _ = plan_v1(snap, geo, ev, a, ind)
    adjusted, changes = traffic.adjust_plan(snap, acts, ev, "flood")
    advisory = [x for x in adjusted if x.action_type == "issue_public_advisory"]
    assert len(advisory) == 1 and advisory[0].proposed_by == "traffic" and "(Simulation)" in advisory[0].params["message"]
    assert {"type": "diversion_advisory"}.items() <= changes[-1].items()
    _, quiet = traffic.adjust_plan(snap, acts, ev, "violence_assault")
    assert not any(c["type"] == "diversion_advisory" for c in quiet)


def test_conflicting_road_and_utility_actions_are_reanalysed_after_revision():
    snap, geo, ev, a, ind = context("flood_conflict")
    v1, _ = plan_v1(snap, geo, ev, a, ind)
    r1 = validate_plan(v1, snap, ev, traffic=traffic.analyse_plan(snap, v1, ev, plan_version=1).as_dict(), plan_version=1)
    assert any(f.rule_id == "R2_INFRA_DEPENDENCY" and f.severity == "blocking" for f in r1.findings)
    out = revise(v1, r1, snap, a, ev, geo, ind, services=select_services("flood", ind, a), category="flood")
    t2 = traffic.analyse_plan(snap, out.actions, ev, plan_version=2)
    v1_route = next(x for x in v1 if x.action_type == "designate_response_route").params["edge_path"]
    v2_route = next(x for x in out.actions if x.action_type == "designate_response_route").params["edge_path"]
    assert v1_route != v2_route and t2.plan_version == 2
    assert validate_plan(out.actions, snap, ev, traffic=t2.as_dict(), plan_version=2).passed


# ---- validator: dispatch consistency ------------------------------------------------------------
def test_duplicate_or_mismatched_dispatch_routes_are_blocked():
    snap, geo, ev, a, services = incident("theft_robbery_burglary", [], "Shop break-in.")
    acts, _ = actions_of(snap, geo, ev, a, [], services)
    route = next(x for x in acts if x.action_type == "designate_response_route")
    dup = ActionSpec(50, route.action_type, route.target_id, dict(route.params), "mobility", "high", "dup")
    r = validate_plan(acts + [dup], snap, ev)
    assert any(f.title == "Duplicate police response route" for f in r.findings) and not r.passed
    wrong = ActionSpec(route.seq, route.action_type, route.target_id, {**route.params, "service": "fire_rescue"},
                       "mobility", "high", "mislabelled")
    r2 = validate_plan([wrong], snap, ev)
    assert any(f.title == "Route service does not match its purpose" for f in r2.findings) and not r2.passed


# ---- provenance, reasons, ranking, misclassification ------------------------------------------------
def test_traffic_output_labels_sources_and_explains_choices():
    snap, geo, ev, a, ind = context("flood_conflict", confirm=True)
    acts, _ = plan_v1(snap, geo, ev, a, ind)
    tp = traffic.analyse_plan(snap, acts, ev, plan_version=1).as_dict()
    types = {s["type"] for s in tp["evidence_sources"]}
    assert {"static map data", "simulated", "unavailable"} <= types and tp["live_traffic"] == "unavailable"
    ra = tp["route_assessments"][0]
    assert "free-flow" in ra["selection_reason"] and "no live traffic" in ra["confidence"]
    assert any("signal-controlled junction" in r for r in ra["risks"])
    assert ra["alternative"]["not_selected_reason"].startswith("Not selected") or "constraints" in ra["alternative"]["not_selected_reason"]
    assert tp["alternative_routes"][0]["geometry"] and len(tp["alternative_routes"][0]["geometry"]) >= 2
    assert tp["affected_junctions"] and not any("congest" in str(v).lower() for v in tp["route_assessments"])


def test_facilities_are_ranked_by_road_travel_time_not_straight_line():
    snap, geo, ev, a, services = incident("medical_emergency", ["person_unconscious"], "A man collapsed.")
    _, mp = actions_of(snap, geo, ev, a, ["person_unconscious"], services)
    recs = [f for f in mp.facility_recommendations if f["kind"] == "hospital"]
    assert [f["rank"] for f in recs] == list(range(1, len(recs) + 1))
    assert [f["travel_s"] for f in recs] == sorted(f["travel_s"] for f in recs)
    assert all(f["lat"] is not None and f["route_distance_m"] > 0 for f in recs)
    # The planned medical route goes to the top-ranked candidate: map, plan and candidate list agree.
    assert mp.routes[0].destination_label == recs[0]["name"]
    assert mp.routes[0].route.travel_s == recs[0]["travel_s"]


def test_a_model_misreading_the_category_does_not_add_services():
    snap, geo, ev, a, _ = incident("theft_robbery_burglary", [], "Someone broke into the shop.")
    misread = a.model_copy(update={"category": "fire"})  # e.g. an LLM mislabels the report as a fire
    st = statuses(select_services("theft_robbery_burglary", [], misread))
    assert st == {"fire_rescue": "not_justified", "ambulance": "not_justified", "police": "required"}


def test_staging_serves_every_required_route_even_when_the_perimeter_is_a_dead_end():
    # Real location where the only perimeter node of the 35 m accident area can be reached but not left
    # without crossing the hazard: the ambulance used to be stranded there with no hospital route.
    snap = baseline_snapshot()
    ind = ["people_trapped", "injuries"]
    geo = incident_geo(snap, 12.8702, 74.8436, "accident", ind)
    ev = assess_evidence(snap, geo, "accident", [], NOW, 30, CONFIRMED)
    a = sentinel.rule_based_triage("Two cars collided, a driver is trapped and people are hurt.", "accident", ind, geo)
    mp = mobility.propose(snap, a, ev, geo, ind, services=select_services("accident", ind, a))
    assert mp.failed == []
    assert {r.purpose for r in mp.routes} == {"emergency_access", "medical_transfer", "police_response"}
    assert len({r.route.node_path[-1] for r in mp.routes if r.purpose in ("emergency_access", "police_response")}
               | {r.route.node_path[0] for r in mp.routes if r.purpose == "medical_transfer"}) == 1  # one shared staging point
    assert not any(set(r.route.edge_path) & set(geo.hazard_segment_ids) for r in mp.routes)

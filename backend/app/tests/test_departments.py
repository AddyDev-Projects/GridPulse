"""Department selection, justification, R9 and per-plan department legs (unit and API level)."""

from datetime import datetime, timezone

from app.agents import mobility, sentinel
from app.agents.evidence import assess_evidence
from app.agents.geo_context import incident_geo
from app.agents.services import plan_departments, select_services
from app.geo.road_graph import mangaluru_graph
from app.orchestration.revision import make_action, resequence
from app.schemas.agents import OperatorConfirmation
from app.simulation.baseline import baseline_snapshot
from app.simulation.state import ActionSpec
from app.tests.test_workflow import citizen_report, incident_id, run
from app.validation.validator import validate_plan

NOW = datetime.now(timezone.utc)
LAT, LON = 12.8702, 74.8436  # a real junction area in Mangaluru with all three facility kinds reachable
CONFIRMED = OperatorConfirmation(operator="ops.test", confirmed_at=NOW, note="Called back")


def decide(category, indicators=(), text="Something happened here."):
    snap = baseline_snapshot()
    geo = incident_geo(snap, LAT, LON, category, list(indicators))
    a = sentinel.rule_based_triage(text, category, list(indicators), geo)
    return select_services(category, list(indicators), a), snap, geo, a


def st(services):
    return {d.service: d.status for d in services.decisions}


# ---- selection ------------------------------------------------------------------------------------
def test_accident_without_fire_evidence_does_not_send_fire_and_rescue():
    s, *_ = decide("accident", (), "Two scooters collided near the bus stop.")
    assert st(s) == {"fire_rescue": "not_justified", "ambulance": "conditional", "police": "required"}
    by = {d.service: d for d in s.decisions}
    assert by["police"].basis == "rule" and "scene safety" in by["police"].rationale
    assert "Whether anyone is injured, and how many" in by["ambulance"].unknowns
    assert "entrapment" in by["fire_rescue"].rationale  # says what is missing, invents nothing


def test_accident_with_entrapment_or_fire_justifies_fire_and_rescue():
    s, *_ = decide("accident", ("people_trapped",), "A car overturned and the driver is stuck inside.")
    assert st(s)["fire_rescue"] == "required"
    assert next(d for d in s.decisions if d.service == "fire_rescue").evidence == ["indicator: people trapped"]


def test_flood_needs_evidence_of_a_rescue_need_before_fire_and_rescue_is_required():
    plain, *_ = decide("flood", (), "Water is pooling on the road after heavy rain.")
    assert st(plain) == {"fire_rescue": "conditional", "ambulance": "not_justified", "police": "recommended"}
    assert "Whether people or vehicles are stranded" in next(d for d in plain.decisions if d.service == "fire_rescue").unknowns
    stranded, *_ = decide("flood", ("water_rising", "vehicles_stranded"), "A car is stuck in rising water.")
    assert st(stranded)["fire_rescue"] == "required"


def test_unclear_report_assigns_no_department_and_plan_is_verification_only():
    s, snap, geo, a = decide("other", (), "Something odd is going on near the market.")
    assert s.required() == []
    ev = assess_evidence(snap, geo, "other", [], NOW, 30, CONFIRMED)
    mp = mobility.propose(snap, a, ev, geo, [], services=s)
    assert mp.routes == [] and not any(x.action_type == "designate_response_route" for x in mp.actions)


def test_every_required_department_has_reason_evidence_and_expected_action():
    for cat, ind in [("fire", ("fire_spreading",)), ("medical_emergency", ()), ("accident", ("injuries",)),
                     ("theft_robbery_burglary", ()), ("flood", ("vehicles_stranded",))]:
        s, *_ = decide(cat, ind)
        for d in s.decisions:
            assert d.rationale.strip()
            if d.status == "required":
                assert d.evidence and d.expected_action and d.basis in ("reported", "rule")


def test_text_mentions_never_make_a_department_required():
    s, *_ = decide("other", (), "There is a fire and someone is hurt and a man has a knife.")
    assert s.required() == [] and {d.basis for d in s.decisions if d.status == "conditional"} == {"inference"}


# ---- R9: the final plan cannot reintroduce unjustified departments ---------------------------------
def _plan(category, indicators=()):
    s, snap, geo, a = decide(category, indicators)
    ev = assess_evidence(snap, geo, category, [], NOW, 30, CONFIRMED)
    mp = mobility.propose(snap, a, ev, geo, list(indicators), services=s)
    return resequence([make_action(i, p) for i, p in enumerate(mp.actions, 1)]), s, mp, snap, ev, geo, a


def test_validator_blocks_a_route_for_a_department_that_is_not_required():
    acts, s, mp, snap, ev, geo, a = _plan("theft_robbery_burglary")
    assert validate_plan(acts, snap, ev, departments=s.as_list(), route_legs=mp.route_legs).passed
    fire_s = select_services("fire", ["fire_spreading"], a)
    fire_route = next(p for p in mobility.propose(snap, a, ev, geo, ["fire_spreading"], services=fire_s).actions
                      if p.action_type == "designate_response_route")
    sneaked = resequence(acts + [make_action(99, fire_route)])
    result = validate_plan(sneaked, snap, ev, departments=s.as_list(), route_legs=mp.route_legs)
    assert not result.passed
    assert any(f.rule_id == "R9_DEPARTMENTS" and f.severity == "blocking" and "Fire & rescue" in f.title for f in result.findings)


def test_unroutable_department_is_reported_not_hidden():
    acts, s, mp, snap, ev, geo, a = _plan("theft_robbery_burglary")
    for sid in snap.graph.segments:  # close every road but the hazard-free incident segment: no police route
        snap.road_status[sid] = "closed"
    mp2 = mobility.propose(snap, a, ev, geo, [], services=s)
    assert "police_response" in mp2.failed and not mp2.routes
    leg = next(x for x in mp2.route_legs if x["service"] == "police")
    assert leg["status"] == "unavailable" and "No valid road route could be calculated" in leg["reason"]
    acts2 = resequence([make_action(i, p) for i, p in enumerate(mp2.actions, 1)])
    depts = plan_departments(s.as_list(), mp2.route_legs, acts2)
    assert next(d for d in depts if d["service"] == "police")["routes"][0]["status"] == "unavailable"


def test_ambulance_legs_are_distinct_and_the_dispatch_origin_is_not_invented():
    acts, s, mp, snap, ev, geo, a = _plan("medical_emergency", ("person_unconscious",))
    amb = next(d for d in plan_departments(s.as_list(), mp.route_legs, acts) if d["service"] == "ambulance")
    legs = {leg["leg"]: leg for leg in amb["routes"]}
    assert legs["to_incident"]["status"] == "unavailable" and "No ambulance station" in legs["to_incident"]["reason"]
    assert legs["to_hospital"]["status"] == "planned" and legs["to_hospital"]["purpose"] == "medical_transfer"
    assert "incident to hospital" in legs["to_hospital"]["name"]
    r = validate_plan(acts, snap, ev, departments=s.as_list(), route_legs=mp.route_legs)
    assert r.passed and any(f.title == "Ambulance starting point unknown" and f.severity == "info" for f in r.findings)


# ---- API: departments and route geometry survive serialisation -----------------------------------
def _run_report(admin, anon, **body):
    inc = incident_id(admin, citizen_report(anon, lat=LAT, lon=LON, **body))
    admin.post(f"/api/incidents/{inc}/verify", json={"note": "test"})
    return run(admin, inc)


def test_api_plan_carries_departments_with_legs_matching_its_actions(admin, anon):
    wf = _run_report(admin, anon, category="accident", description="Two scooters collided near the bus stop.", indicators=[])
    plan = wf["plans"][-1]
    by = {d["service"]: d for d in plan["departments"]}
    assert by["police"]["status"] == "required" and by["fire_rescue"]["status"] == "not_justified"
    assert by["fire_rescue"]["routes"] == [] and by["ambulance"]["routes"] == []
    police_leg = by["police"]["routes"][0]
    route = next(a for a in plan["actions"] if a["seq"] == police_leg["action_seq"])
    assert route["params"]["service"] == "police" and police_leg["status"] == "planned"
    assert not any(a["params"].get("service") == "fire_rescue" for a in plan["actions"])


def test_api_route_geometry_follows_the_road_graph_in_lat_lon_order(admin, anon):
    wf = _run_report(admin, anon, category="accident", description="Car hit a pole, driver trapped, people hurt.",
                     indicators=["people_trapped", "injuries"])
    plan = wf["plans"][-1]
    g = mangaluru_graph()
    s, w, n, e = g.bbox
    routes = [a for a in plan["actions"] if a["action_type"] == "designate_response_route"]
    assert {r["params"]["service"] for r in routes} == {"fire_rescue", "police", "ambulance"}
    for r in routes:
        geom = r["geometry"]
        assert len(geom) >= 2 and all(s - 0.01 <= lat <= n + 0.01 and w - 0.01 <= lon <= e + 0.01 for lat, lon in geom)
        assert g.is_continuous(r["params"]["node_path"], r["params"]["edge_path"]) == []
        assert geom == [list(p) for p in g.path_geometry(r["params"]["node_path"], r["params"]["edge_path"])]

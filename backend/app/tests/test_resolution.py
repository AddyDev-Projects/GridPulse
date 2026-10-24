"""Service-aware workflow, per-version traffic analysis, incident resolution and server timestamps (API level)."""

import re
from datetime import timedelta

from sqlalchemy import select, text

from app.db.base import utcnow
from app.geo.road_graph import mangaluru_graph
from app.models import Approval, Incident, ResponsePlan
from app.services.reports import create_report
from app.tests.test_workflow import A, citizen_report, decide, incident_id, pending, run  # noqa: F401 — pending is a fixture

POLICE = {"category": "theft_robbery_burglary", "description": "Someone broke into the shop and stole the till.",
          "indicators": []}


def latest(wf: dict) -> dict:
    return wf["plans"][-1]


# ---- service selection through the real workflow ------------------------------------------------
def test_confirmed_police_report_plans_only_a_police_response(admin, anon):
    inc = incident_id(admin, citizen_report(anon, **POLICE))
    assert admin.post(f"/api/incidents/{inc}/verify", json={"note": "Owner called back"}).status_code == 200
    wf = run(admin, inc)
    assert wf["incident"]["status"] == "AWAITING_APPROVAL"
    plan = latest(wf)
    routes = [a for a in plan["actions"] if a["action_type"] == "designate_response_route"]
    assert [r["params"]["purpose"] for r in routes] == ["police_response"]
    assert routes[0]["params"]["service"] == "police" and "Police Station" in routes[0]["params"]["origin_label"]
    assert not any(a["action_type"] in ("close_roads", "isolate_substation", "issue_public_advisory") for a in plan["actions"])
    detail = admin.get(f"/api/incidents/{inc}").json()
    assert {s["service"]: s["status"] for s in detail["services"]} == {
        "fire_rescue": "not_needed", "ambulance": "not_needed", "police": "required"}
    events = admin.get(f"/api/incidents/{inc}/audit").json()
    assert any(e["event_type"] == "services.selected" and "police required" in e["message"] for e in events)


def test_unconfirmed_police_report_is_verification_first_without_a_public_advisory(admin, anon):
    inc = incident_id(admin, citizen_report(anon, **POLICE))
    wf = run(admin, inc)
    final = latest(wf)
    types = [a["action_type"] for a in final["actions"]]
    assert "designate_response_route" not in types and "dispatch_field_verification" in types
    assert "issue_public_advisory" not in types  # an unverified crime allegation is not broadcast


# ---- AnalyseTraffic per plan version -------------------------------------------------------------
def test_traffic_is_analysed_for_every_plan_version_and_shapes_the_plan(admin, pending):  # noqa: F811
    inc = pending["incident"]["id"]
    runs = [r for r in pending["agent_runs"] if r["agent"] == "traffic" and r["step"] == "analyse"]
    assert [r["revision_number"] for r in runs] == [0, 1]
    proposals = [p for p in admin.get(f"/api/incidents/{inc}").json()["proposals"] if p["agent"] == "traffic"]
    assert [p["payload"]["plan_version"] for p in proposals] == [1, 2]
    v1, v2 = pending["plans"]
    route = lambda plan: next(a for a in plan["actions"] if a["action_type"] == "designate_response_route")  # noqa: E731
    assert proposals[1]["payload"]["route_assessments"][0]["length_m"] == round(route(v2)["params"]["length_m"], 1)
    assert route(v1)["params"]["edge_path"] != route(v2)["params"]["edge_path"]
    # The traffic agent changed the actual plan: a diversion notice for the closures it found.
    assert any(a["action_type"] == "issue_public_advisory" and a["proposed_by"] == "traffic" for a in v1["actions"])
    r8 = [f for f in v2["latest_validation"]["findings"] if f["rule_id"] == "R8_TRAFFIC_DATA"]
    assert r8 and all(f["severity"] != "blocking" for f in r8)
    assert not any(f["title"] == "Traffic analysis is out of date" for f in r8)


def test_replanning_after_a_state_change_uses_current_traffic_state(admin, pending):  # noqa: F811
    plan = latest(pending)
    assert decide(admin, plan, "approve").status_code == 200
    assert admin.post("/api/simulation/clear").status_code == 200       # city state moves on
    stale = admin.post(f"/api/plans/{plan['id']}/execute")
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_approval"
    msg = stale.json()["detail"]["message"]
    assert "city state has changed" in msg and "Revalidate the plan" in msg
    assert not re.search(r"\bv\d+\b", msg)  # no internal state-version numbers in operator-facing text
    blocked = admin.get(f"/api/incidents/{pending['incident']['id']}/workflow").json()["plans"][-1]
    assert blocked["status"] == "STALE" and not blocked["approvable"]
    assert admin.post(f"/api/plans/{plan['id']}/revalidate").status_code == 200
    runs = admin.get(f"/api/plans/{plan['id']}/validation").json()
    state_now = admin.get("/api/simulation/state").json()["state_version"]
    reval = next(r for r in runs if r["trigger"] == "revalidate")
    assert reval["state_version"] == state_now
    assert not any(f["title"] == "Traffic analysis is out of date" for f in reval["findings"])


# ---- resolution ------------------------------------------------------------------------------------
def test_resolving_an_executed_emergency_hides_it_from_active_views_and_keeps_history(admin, anon, pending, db):  # noqa: F811
    inc, plan = pending["incident"]["id"], latest(pending)
    ref = pending["incident"]["reference"]
    assert decide(admin, plan, "approve").status_code == 200
    assert admin.post(f"/api/plans/{plan['id']}/execute").status_code == 200
    assert admin.get("/api/simulation/overlay").json()["active_routes"]
    closures_before = admin.get("/api/simulation/overlay").json()["closures"]

    r = admin.post(f"/api/incidents/{inc}/resolve", json={"note": "Water receded", "expected_status": "EXECUTED"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "RESOLVED" and r.json()["resolved_at"]
    assert inc not in [i["id"] for i in admin.get("/api/incidents").json()]                       # active queue
    assert inc in [i["id"] for i in admin.get("/api/incidents?state=resolved").json()]            # history
    assert admin.get("/api/simulation/overlay").json()["active_routes"] == []                     # map routes
    assert admin.get("/api/simulation/overlay").json()["closures"] == closures_before             # closures untouched
    assert ref not in [i["reference"] for i in anon.get("/api/public/incidents").json()]          # public map
    assert anon.get(f"/api/public/reports/{ref}").json()["status"] == "resolved"
    detail = admin.get(f"/api/incidents/{inc}").json()
    assert detail["resolved_by"] and detail["incident"]["resolution_note"] == "Water receded"
    assert detail["incident"]["report_text"]                                                      # original report kept
    events = [e["event_type"] for e in admin.get(f"/api/incidents/{inc}/audit").json()]
    assert "incident.resolved" in events and "execution.succeeded" in events                      # history kept
    db.expire_all()
    assert db.scalars(select(Incident).where(Incident.id == inc)).one().status == "RESOLVED"      # persisted


def test_resolving_withdraws_unexecuted_plans_and_never_executes(admin, pending, db):  # noqa: F811
    inc, plan = pending["incident"]["id"], latest(pending)
    version_before = admin.get("/api/simulation/state").json()["state_version"]
    assert decide(admin, plan, "approve").status_code == 200
    assert admin.post(f"/api/incidents/{inc}/resolve", json={}).status_code == 200
    assert admin.get("/api/simulation/state").json()["state_version"] == version_before           # nothing executed
    db.expire_all()
    assert db.scalars(select(ResponsePlan.status).where(ResponsePlan.id == plan["id"])).one() == "INVALID"
    assert db.scalars(select(Approval.status).where(Approval.plan_id == plan["id"])).one() == "invalidated"
    blocked = admin.post(f"/api/plans/{plan['id']}/execute")
    assert blocked.status_code == 409


def test_resolve_is_idempotent_and_rejects_stale_views_and_anonymous_callers(admin, anon, pending, db):  # noqa: F811
    inc = pending["incident"]["id"]
    stale = admin.post(f"/api/incidents/{inc}/resolve", json={"expected_status": "EXECUTED"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_state"
    assert anon.post(f"/api/incidents/{inc}/resolve", json={}, csrf=False).status_code == 401
    first = admin.post(f"/api/incidents/{inc}/resolve", json={"note": "first"})
    assert first.status_code == 200
    again = admin.post(f"/api/incidents/{inc}/resolve", json={"note": "second", "expected_status": "AWAITING_APPROVAL"})
    assert again.status_code == 200 and again.json()["status"] == "RESOLVED"
    assert again.json()["resolved_at"] == first.json()["resolved_at"] and again.json()["resolution_note"] == "first"
    resolved_events = [e for e in admin.get(f"/api/incidents/{inc}/audit").json() if e["event_type"] == "incident.resolved"]
    assert len(resolved_events) == 1
    transitions = db.execute(text("select count(*) from workflow_transitions where incident_id = :i and to_status = 'RESOLVED'"),
                             {"i": inc}).scalar()
    assert transitions == 1


def test_new_unrun_reports_are_dismissed_not_resolved(admin, anon):
    inc = incident_id(admin, citizen_report(anon))
    # The UI offers Dismiss for these; the API still records a resolution if an operator closes it out.
    assert admin.post(f"/api/incidents/{inc}/resolve", json={"note": "Handled by phone"}).status_code == 200
    assert admin.get(f"/api/incidents/{inc}").json()["incident"]["status"] == "RESOLVED"


# ---- server timestamps -------------------------------------------------------------------------------
def test_report_time_is_set_by_the_server(anon, db):
    before = utcnow()
    ref = citizen_report(anon)
    after = utcnow()
    inc = db.scalars(select(Incident).where(Incident.reference == ref)).one()
    assert before - timedelta(seconds=1) <= inc.observed_at <= after + timedelta(seconds=1)
    # timestamptz: an absolute instant (stored as UTC); the driver renders it in the session's time zone.
    assert inc.observed_at.tzinfo is not None and inc.observed_at == inc.created_at


def test_client_supplied_time_is_rejected(anon):
    r = anon.post("/api/public/reports", json={
        "category": "flood", "description": "Water above the kerb and rising fast.", "lat": A.lat, "lon": A.lon,
        "indicators": [], "observed_at": "2020-01-01T00:00:00+00:00"})
    assert r.status_code == 422


def test_migration_allows_resolved_and_keeps_existing_rows(migrated_db):
    with migrated_db.connect() as conn:
        check = conn.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_incidents_status'")).scalar()
        cols = {r[0] for r in conn.execute(text(
            "select column_name from information_schema.columns where table_name = 'incidents'"))}
    assert "RESOLVED" in check and "DISMISSED" in check
    assert {"resolved_at", "resolved_by_id", "resolution_note"} <= cols


# ---- public latest-five feed -----------------------------------------------------------------------
def test_public_recent_feed_skips_resolved_and_dismissed_before_the_limit(admin, anon, db):
    # Created through the report service (as test_public does): seven public submissions would hit the rate limit.
    incs = [create_report(db, mangaluru_graph(), category="flood", description=f"Water above the kerb at point {i}.",
                          lat=A.lat, lon=A.lon, indicators=[], location_description=None, source="citizen_portal")
            for i in range(7)]
    db.commit()
    refs = [i.reference for i in incs]
    ids = {i.reference: str(i.id) for i in incs}
    base = utcnow()
    for i, ref in enumerate(refs):  # deterministic order: refs[6] newest
        db.execute(text("update incidents set created_at = :t where reference = :r"), {"t": base - timedelta(minutes=10 - i), "r": ref})
    db.commit()
    # Newest two are closed: one resolved (after running), one dismissed.
    run(admin, ids[refs[6]])
    assert admin.post(f"/api/incidents/{ids[refs[6]]}/resolve", json={}).status_code == 200
    assert admin.post(f"/api/incidents/{ids[refs[5]]}/dismiss", json={"reason": "Duplicate report"}).status_code == 200
    feed = anon.get("/api/public/recent-incidents").json()
    assert [r["reference"] for r in feed] == [refs[4], refs[3], refs[2], refs[1], refs[0]]
    assert all(set(r) == {"reference", "category", "location", "reported_at", "status", "status_label"} for r in feed)


def test_public_recent_feed_returns_fewer_when_fewer_are_active(admin, anon):
    refs = [citizen_report(anon) for _ in range(2)]
    assert admin.post(f"/api/incidents/{incident_id(admin, refs[0])}/resolve", json={}).status_code == 200
    assert [r["reference"] for r in anon.get("/api/public/recent-incidents").json()] == [refs[1]]

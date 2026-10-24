"""End-to-end workflow through the HTTP API against PostgreSQL, plus integrity safeguards."""

import threading

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.base import Base
from app.db.session import session_factory
from app.geo.road_graph import mangaluru_graph
from app.main import app
from app.models import AuditEvent, Incident, RoadClosure, SimulationExecution
from app.simulation.scenarios import SCENARIOS
from app.tests.conftest import Api, alembic_config, make_operator

A = SCENARIOS["flood_conflict"]


def citizen_report(anon: Api, **over) -> str:
    body = {"category": "flood", "description": "Water above the kerb, a car is stuck and it keeps rising.",
            "lat": A.lat, "lon": A.lon, "indicators": ["water_rising", "vehicles_stranded"], **over}
    r = anon.post("/api/public/reports", json=body)
    assert r.status_code == 201, r.text
    return r.json()["reference"]


def incident_id(api: Api, reference: str) -> str:
    return next(i["id"] for i in api.get("/api/incidents").json() if i["reference"] == reference)


def run(api: Api, inc: str) -> dict:
    assert api.post(f"/api/incidents/{inc}/run").status_code == 202
    return api.get(f"/api/incidents/{inc}/workflow").json()


def decide(api: Api, plan: dict, action: str, **extra):
    return api.post(f"/api/plans/{plan['id']}/{action}",
                    json={"plan_version": plan["version"], "content_hash": plan["content_hash"], **extra})


def city(api: Api) -> tuple:
    o = api.get("/api/simulation/overlay").json()
    return ({n["id"]: n["status"] for n in o["nodes"]}, sorted(c["segment_id"] for c in o["closures"]),
            len(o["active_routes"]), o["state_version"])


# ---- the complete flow ---------------------------------------------------------------------
def test_citizen_report_to_operator_approval_end_to_end(admin, anon, db):
    assert admin.post("/api/simulation/scenarios/flood_conflict/load", json={"create_report": False}).status_code == 200
    ref = citizen_report(anon)                                       # 1-2 citizen submits, gets a reference
    inc = incident_id(admin, ref)                                     # 3-4 operator sees it in the queue
    before = city(admin)
    detail = admin.get(f"/api/incidents/{inc}").json()
    assert detail["incident"]["lat"] == pytest.approx(A.lat, abs=1e-6) and detail["assessment"] is None

    wf = run(admin, inc)                                              # 5-7 agents, coordinator, validator
    assert [t["to_status"] for t in wf["transitions"]] == [
        "REPORTED", "TRIAGED", "EVIDENCE_GATHERED", "PLANS_PROPOSED", "VALIDATING", "REVISION_REQUIRED",
        "VALIDATING_AGAIN", "AWAITING_APPROVAL"]
    v1, v2 = wf["plans"]
    assert v1["status"] == "SUPERSEDED"
    assert any(f["rule_id"] == "R2_INFRA_DEPENDENCY" and f["severity"] == "blocking" for f in v1["latest_validation"]["findings"])
    assert v2["latest_validation"]["passed"] and v2["approvable"] and v1["content_hash"] != v2["content_hash"]
    assert city(admin)[:3] == before[:3], "nothing changes before approval"

    g = mangaluru_graph()                                             # displayed route == computed route
    route = next(a for a in v2["actions"] if a["action_type"] == "designate_response_route")
    expected = [list(p) for p in g.path_geometry(route["params"]["node_path"], route["params"]["edge_path"])]
    assert route["geometry"] == expected

    assert decide(admin, v2, "approve").status_code == 200           # 8 exact version approved
    r = admin.post(f"/api/plans/{v2['id']}/execute")                  # 9 simulated execution
    assert r.status_code == 200, r.text
    nodes, closures, routes, version = city(admin)
    closed = next(a for a in v2["actions"] if a["action_type"] == "close_roads")["params"]["segment_ids"]
    assert set(closed) <= set(closures) and nodes["SIM-FZ-2B"] if "SIM-FZ-2B" in nodes else True
    assert routes >= 1 and version == before[3] + 1
    assert anon.get(f"/api/public/reports/{ref}").json()["status"] == "action_recorded"
    assert {c["segment_id"] for c in anon.get("/api/public/closures").json()} >= set(closed)

    events = [e["event_type"] for e in admin.get(f"/api/incidents/{inc}/audit").json()]   # 10 audit
    for expected_event in ["report.submitted", "workflow.requested", "sentinel.assessment", "evidence.retrieved",
                           "mobility.proposal", "grid.proposal", "plan.created", "validation.finding", "plan.revised",
                           "validation.completed", "approval.granted", "execution.succeeded"]:
        assert expected_event in events
    appr = db.execute(text("select operator, operator_id from approvals")).one()
    assert appr.operator == "admin.ravi" and appr.operator_id is not None


def test_real_report_without_sensors_gets_verification_first(operator, anon):
    inc = incident_id(operator, citizen_report(anon))
    wf = run(operator, inc)
    final = wf["plans"][-1]
    assert wf["incident"]["status"] == "AWAITING_APPROVAL"
    assert {a["action_type"] for a in final["actions"]} == {"set_roads_caution", "dispatch_field_verification",
                                                           "issue_public_advisory"}
    ev = operator.get(f"/api/incidents/{inc}").json()["evidence"]
    assert ev["location_hazard"]["status"] == "UNCORROBORATED" and ev["observations"] == []


def test_operator_confirmation_enables_closures_and_routes(operator, anon):
    inc = incident_id(operator, citizen_report(anon))
    assert operator.post(f"/api/incidents/{inc}/verify", json={"note": "Field team confirmed"}).status_code == 200
    wf = run(operator, inc)
    final = wf["plans"][-1]
    types = {a["action_type"] for a in final["actions"]}
    assert {"close_roads", "designate_response_route"} <= types and final["approvable"]
    ev = operator.get(f"/api/incidents/{inc}").json()["evidence"]
    assert ev["location_hazard"]["status"] == "OPERATOR_CONFIRMED"


def test_rejection_leaves_simulated_city_unchanged(admin, anon):
    admin.post("/api/simulation/scenarios/approval_rejection/load", json={"create_report": True})
    inc = admin.get("/api/incidents").json()[0]["id"]
    plan = run(admin, inc)["plans"][-1]
    before = city(admin)
    assert decide(admin, plan, "reject", note="Prefer manual coordination").json()["status"] == "REJECTED"
    assert admin.post(f"/api/plans/{plan['id']}/execute").status_code == 409
    assert decide(admin, plan, "approve").status_code == 409
    assert city(admin) == before


def test_no_safe_route_escalates_without_changes(admin):
    admin.post("/api/simulation/scenarios/no_safe_plan/load", json={"create_report": True})
    inc = admin.get("/api/incidents").json()[0]["id"]
    before = city(admin)
    wf = run(admin, inc)
    assert wf["incident"]["status"] == "NO_SAFE_PLAN"
    plan = wf["plans"][-1]
    assert plan["status"] == "ESCALATED" and not plan["approvable"]
    assert any(f["rule_id"] == "R7_UNRESOLVED_CONFLICT" for f in plan["latest_validation"]["findings"])
    assert decide(admin, plan, "approve").status_code == 409
    assert city(admin) == before


# ---- approval safeguards -------------------------------------------------------------------
@pytest.fixture()
def pending(admin):
    admin.post("/api/simulation/scenarios/flood_conflict/load", json={"create_report": True})
    inc = admin.get("/api/incidents").json()[0]["id"]
    return run(admin, inc)


def test_unapproved_and_superseded_plans_cannot_execute(admin, pending):
    v1, v2 = pending["plans"]
    assert admin.post(f"/api/plans/{v2['id']}/execute").json()["detail"]["code"] == "not_approved"
    assert decide(admin, v1, "approve").json()["detail"]["code"] == "invalid_state"


def test_approval_is_bound_to_exact_version_and_hash(admin, pending):
    v1, v2 = pending["plans"]
    r = admin.post(f"/api/plans/{v2['id']}/approve", json={"plan_version": 2, "content_hash": v1["content_hash"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_mismatch"


def test_stale_approval_cannot_authorize_after_state_change(admin, pending):
    plan = pending["plans"][-1]
    assert decide(admin, plan, "approve").status_code == 200
    admin.post("/api/simulation/clear")
    r = admin.post(f"/api/plans/{plan['id']}/execute")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_approval"
    p = admin.get(f"/api/plans/{plan['id']}").json()
    assert p["status"] == "STALE" and p["approvals"][0]["status"] == "invalidated"


def test_a_second_operator_cannot_reuse_or_duplicate_an_approval(admin, pending, db):
    plan = pending["plans"][-1]
    pw = make_operator(db, "ops.second")
    with TestClient(app) as c:
        other = Api(c)
        other.login("ops.second", pw)
        assert decide(admin, plan, "approve").status_code == 200
        assert decide(other, plan, "approve").status_code == 409


def test_duplicate_and_concurrent_execution(admin, pending):
    plan = pending["plans"][-1]
    decide(admin, plan, "approve")
    codes = []

    def go():
        codes.append(admin.post(f"/api/plans/{plan['id']}/execute").status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409, 409]
    s = session_factory()()
    assert s.scalar(select(func.count()).select_from(SimulationExecution)) == 1
    s.close()


def test_failed_execution_rolls_back(admin, pending, monkeypatch):
    plan = pending["plans"][-1]
    decide(admin, plan, "approve")
    before = city(admin)
    from app.services import decisions

    real = decisions.persist_changes

    def explode(db, changes, **kw):
        real(db, changes[:1], **kw)
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(decisions, "persist_changes", explode)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.cookies = admin.c.cookies
        r = c.post(f"/api/plans/{plan['id']}/execute", headers={"X-CSRF-Token": admin.csrf})
    assert r.status_code == 500 and "disk" not in r.text
    assert city(admin) == before
    monkeypatch.setattr(decisions, "persist_changes", real)
    assert admin.post(f"/api/plans/{plan['id']}/execute").status_code == 200


def test_provider_failure_does_not_bypass_validation(admin, monkeypatch):
    from app.orchestration import coordinator
    from app.tests.test_llm_provider import FakePost, GEMINI_BAD_MODEL, settings
    import httpx

    from app.llm import provider as llm

    monkeypatch.setattr(llm.httpx, "post", FakePost(httpx.Response(400, json=GEMINI_BAD_MODEL)))
    monkeypatch.setattr(coordinator, "get_provider", lambda s=None: llm.OpenAICompatibleProvider(settings("bad model")))
    admin.post("/api/simulation/scenarios/flood_conflict/load", json={"create_report": True})
    inc = admin.get("/api/incidents").json()[0]["id"]
    wf = run(admin, inc)
    triage = next(r for r in wf["agent_runs"] if r["step"] == "triage")
    assert triage["fallback_used"] and "unexpected model name format" in triage["fallback_reason"]
    assert not wf["plans"][0]["latest_validation"]["passed"], "conflict still caught deterministically"


# ---- workflow state rules -------------------------------------------------------------------
def test_dismiss_and_double_run_rules(operator, anon):
    a = incident_id(operator, citizen_report(anon))
    assert operator.post(f"/api/incidents/{a}/dismiss", json={"reason": "Duplicate of an earlier report"}).status_code == 200
    assert operator.post(f"/api/incidents/{a}/run").status_code == 409
    b = incident_id(operator, citizen_report(anon))
    run(operator, b)
    assert operator.post(f"/api/incidents/{b}/run").status_code == 409


def test_legacy_incidents_are_read_only(operator, db):
    db.execute(text("INSERT INTO incidents (id, epoch, scenario_key, reporter_alias, report_text, status, source, "
                    "indicators, created_at, updated_at) VALUES (gen_random_uuid(), 1, 'flood_conflict', 'legacy', "
                    "'Legacy fictional city report text', 'REPORTED', 'legacy', '[]', now(), now())"))
    db.commit()
    legacy = next(i for i in operator.get("/api/incidents?scope=all").json() if i["legacy"])
    assert operator.post(f"/api/incidents/{legacy['id']}/run").json()["detail"]["code"] == "legacy_read_only"


def test_startup_creates_no_incidents_sensors_or_closures(db):
    with TestClient(app):
        pass
    assert db.scalar(select(func.count()).select_from(Incident)) == 0
    assert db.scalar(select(func.count()).select_from(RoadClosure)) == 0
    assert db.execute(text("select count(*) from infrastructure_nodes where kind='sensor'")).scalar() == 0


def test_audit_trail_is_append_only(db, operator):
    assert db.scalar(select(func.count()).select_from(AuditEvent)) > 0
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE audit_events SET message = 'tampered'"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()


def test_migrations_match_models_and_roundtrip(migrated_db):
    with migrated_db.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    cfg = alembic_config()
    command.downgrade(cfg, "0001")
    command.upgrade(cfg, "head")
    with migrated_db.connect() as conn:
        checks = {r[0] for r in conn.execute(text(
            "select conname from pg_constraint where conrelid = 'incidents'::regclass and contype = 'c'"))}
    assert {"ck_incidents_status", "ck_incidents_source", "ck_incidents_coordinates"} <= checks

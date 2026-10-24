"""Anonymous citizen portal: reporting without login, validation, abuse controls, safe public views."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.geo.road_graph import mangaluru_graph
from app.models import AuditEvent, Incident
from app.services.reports import create_report
from app.simulation.scenarios import SCENARIOS

A = SCENARIOS["flood_conflict"]
REPORT = {"category": "flood", "description": "Water is above the kerb on this road and still rising.",
          "lat": A.lat, "lon": A.lon, "indicators": ["water_rising"], "location_description": "Near the bus stop"}

PRIVATE_KEYS = {"report_text", "client_key_hash", "assessment", "evidence", "proposals", "agent_runs", "plans",
                "operator", "verified_by", "dismissal_reason", "segment_id", "status_reason"}


def test_citizen_reports_without_login(anon, db):
    assert not anon.c.cookies
    r = anon.post("/api/public/reports", json=REPORT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["reference"].startswith("GP-") and len(body["reference"]) == 11
    assert body["status"] == "received" and body["nearest_road"]
    inc = db.scalars(select(Incident).where(Incident.reference == body["reference"])).one()
    assert inc.source == "citizen_portal" and inc.status == "REPORTED" and inc.segment_id
    assert inc.client_key_hash and "testclient" not in inc.client_key_hash, "client address stored only as HMAC"
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "report.submitted")).one()
    assert REPORT["description"] not in event.message


def test_report_status_lookup_is_public_and_minimal(anon):
    ref = anon.post("/api/public/reports", json=REPORT).json()["reference"]
    r = anon.get(f"/api/public/reports/{ref.lower()}")
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert not PRIVATE_KEYS & set(r.json())
    assert anon.get("/api/public/reports/GP-00000000").status_code == 404
    assert anon.get("/api/public/reports/../../etc").status_code == 404


@pytest.mark.parametrize("patch,code", [
    ({"description": "short"}, 422),
    ({"description": "x" * 2001}, 422),
    ({"category": "alien_invasion"}, 422),
    ({"lat": 12.97, "lon": 77.59}, 422),            # Bengaluru: outside coverage
    ({"indicators": ["made_up"]}, 422),
    ({"observed_at": "2099-01-01T00:00:00+00:00"}, 422),
    ({"observed_at": "2026-01-01T00:00:00"}, 422),  # no timezone
    ({"unexpected": "field"}, 422),
    ({"website": "http://spam.example"}, 400),      # honeypot
])
def test_invalid_reports_are_rejected(anon, db, patch, code):
    r = anon.post("/api/public/reports", json={**REPORT, **patch})
    assert r.status_code == code, r.text
    assert db.scalars(select(Incident)).first() is None, "nothing stored on rejection"


def test_outside_coverage_error_explains_why(anon):
    r = anon.post("/api/public/reports", json={**REPORT, "lat": 12.97, "lon": 77.59})
    assert r.json()["detail"]["code"] == "outside_coverage"


def test_report_flooding_is_rate_limited(anon, db):
    for _ in range(5):
        assert anon.post("/api/public/reports", json=REPORT).status_code == 201
    r = anon.post("/api/public/reports", json=REPORT)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0 and "112" in r.json()["detail"]["message"]
    assert db.query(Incident).count() == 5


def test_oversized_bodies_are_refused(anon):
    r = anon.post("/api/public/reports", json={**REPORT, "description": "x" * 70_000})
    assert r.status_code == 413


def test_public_incident_map_shows_only_verified_items_without_internals(anon, operator):
    ref = anon.post("/api/public/reports", json=REPORT).json()["reference"]
    assert anon.get("/api/public/incidents").json() == [], "unverified reports are not public"
    inc_id = next(i["id"] for i in operator.get("/api/incidents").json() if i["reference"] == ref)
    assert operator.post(f"/api/incidents/{inc_id}/verify", json={"note": "Confirmed by phone"}).status_code == 200
    items = anon.get("/api/public/incidents").json()
    assert len(items) == 1 and items[0]["verified"] and items[0]["status"] == "confirmed"
    assert items[0]["lat"] == round(A.lat, 3), "coordinates are coarsened"
    assert not PRIVATE_KEYS & set(items[0])
    assert anon.get(f"/api/public/reports/{ref}").json()["status"] == "confirmed"


def test_public_city_and_segments_come_from_the_real_graph(anon):
    city = anon.get("/api/public/city").json()
    assert city["name"] == "Mangaluru" and "OpenStreetMap" in city["attribution"]
    s, w, n, e = city["coverage_bbox"]
    seg = anon.get(f"/api/public/map/segments?south={A.lat - 0.003}&west={A.lon - 0.003}"
                   f"&north={A.lat + 0.003}&east={A.lon + 0.003}").json()
    assert seg["segments"] and all(len(x["geometry"]) >= 2 for x in seg["segments"])
    assert anon.get(f"/api/public/map/segments?south={s}&west={w}&north={n}&east={e}").status_code == 422


def test_public_cannot_see_simulated_infrastructure(anon):
    assert anon.get("/api/simulation/overlay").status_code == 401
    assert anon.get("/api/public/closures").json() == []


def test_police_category_is_accepted_and_recent_feed_is_limited_and_sanitized(anon, db):
    police = anon.post("/api/public/reports", json={
        **REPORT,
        "category": "homicide",
        "description": "A possible homicide has been reported; do not approach the location.",
        "indicators": ["suspected_homicide"],
    })
    assert police.status_code == 201, police.text
    police_ref = police.json()["reference"]
    stored = db.scalars(select(Incident).where(Incident.reference == police_ref)).one()
    assert stored.category == "homicide" and stored.source == "citizen_portal"
    assert anon.get(f"/api/public/reports/{police_ref}").json()["category"] == "police_assistance"

    base = utcnow()
    for i in range(6):
        inc = create_report(
            db,
            mangaluru_graph(),
            category="flood",
            description=f"A public test report number {i} with enough detail.",
            lat=A.lat,
            lon=A.lon,
            indicators=[],
            location_description=None,
            observed_at=None,
            source="citizen_portal",
        )
        inc.created_at = base - timedelta(minutes=6 - i)
    db.commit()

    response = anon.get("/api/public/recent-incidents")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 5
    assert [row["reported_at"] for row in rows] == sorted((row["reported_at"] for row in rows), reverse=True)
    assert all(set(row) == {"reference", "category", "location", "reported_at", "status", "status_label"} for row in rows)
    assert not PRIVATE_KEYS & set(rows[0])
    assert any(row["reference"] == police_ref and row["category"] == "police_assistance" and row["location"] == "Mangaluru area" for row in rows)

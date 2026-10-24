"""Operator authentication, sessions, CSRF, RBAC and provisioning — all enforced by the backend."""

import io
import uuid

import pytest
from sqlalchemy import select

from app.cli import main as cli
from app.core.security import hash_password, password_problems, verify_password
from app.models import AuditEvent, Operator, OperatorSession
from app.tests.conftest import make_operator

OPERATOR_ROUTES = [
    ("get", "/api/incidents"), ("get", "/api/simulation/state"), ("get", "/api/simulation/overlay"),
    ("get", "/api/audit"), ("get", "/api/scenarios"), ("get", "/api/auth/me"),
]
WRITE_ROUTES = [
    "/api/simulation/scenarios/flood_conflict/load", "/api/simulation/clear",
    f"/api/incidents/{uuid.uuid4()}/run", f"/api/incidents/{uuid.uuid4()}/verify",
    f"/api/plans/{uuid.uuid4()}/approve", f"/api/plans/{uuid.uuid4()}/reject",
    f"/api/plans/{uuid.uuid4()}/execute", f"/api/plans/{uuid.uuid4()}/revalidate",
]


def test_passwords_are_argon2id_and_policy_enforced():
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$") and "correct" not in h
    assert verify_password(h, "correct horse battery") and not verify_password(h, "wrong")
    assert not verify_password(None, "anything")
    assert password_problems("amara", "short")
    assert password_problems("amara", "amara-is-my-password")
    assert password_problems("x", "aaaaaaaaaaaaaaaa")
    assert password_problems("x", "Tannirbhavi-Beach-42") == []


@pytest.mark.parametrize("method,url", OPERATOR_ROUTES)
def test_anonymous_users_cannot_read_operator_data(anon, method, url):
    r = getattr(anon, method)(url)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


@pytest.mark.parametrize("url", WRITE_ROUTES)
def test_anonymous_users_cannot_perform_operator_actions(anon, url):
    body = {"plan_version": 1, "content_hash": "0" * 64} if url.endswith(("approve", "reject")) else {}
    r = anon.post(url, json=body)
    assert r.status_code == 401


def test_forged_cookies_and_role_fields_are_ignored(anon, db):
    anon.c.cookies.set("gp_session", "forged-token", path="/api")
    anon.c.cookies.set("role", "admin")
    r = anon.post(f"/api/plans/{uuid.uuid4()}/approve", json={"plan_version": 1, "content_hash": "0" * 64},
                  headers={"X-CSRF-Token": "x", "X-Role": "admin"})
    assert r.status_code == 401


def test_login_sets_hardened_cookies_and_me_works(anon, db):
    pw = make_operator(db, "ops.priya")
    r = anon.login("ops.priya", pw)
    assert r.status_code == 200 and r.json()["operator"]["role"] == "operator"
    cookies = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("gp_session="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie and "Path=/api" in session_cookie
    assert anon.get("/api/auth/me").json()["username"] == "ops.priya"
    sess = db.scalars(select(OperatorSession)).one()
    token = session_cookie.split(";")[0].split("=", 1)[1]
    assert token not in sess.token_hash, "only the hash is stored"


def test_login_errors_do_not_reveal_accounts(anon, db):
    make_operator(db, "ops.kiran")
    wrong = anon.login("ops.kiran", "definitely-wrong-password")
    unknown = anon.login("nobody.here", "definitely-wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    events = [e.message for e in db.scalars(select(AuditEvent).where(AuditEvent.event_type == "auth.login_failed"))]
    assert len(events) == 2 and not any("definitely" in m for m in events)


def test_login_brute_force_is_throttled(anon, db):
    pw = make_operator(db, "ops.lata")
    for _ in range(5):
        assert anon.login("ops.lata", "wrong-password-123").status_code == 401
    r = anon.login("ops.lata", pw)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_disabled_operator_cannot_login_and_sessions_are_revoked(operator, db):
    assert operator.get("/api/incidents").status_code == 200
    assert cli(["operator", "disable", "ops.amara"]) == 0
    assert operator.get("/api/incidents").status_code == 401
    assert cli(["operator", "enable", "ops.amara"]) == 0


def test_logout_revokes_the_session(operator):
    assert operator.post("/api/auth/logout").status_code == 204
    assert operator.get("/api/incidents").status_code == 401


def test_state_changing_requests_require_csrf(operator):
    r = operator.post("/api/simulation/clear", csrf=False)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf_failed"
    r = operator.post("/api/simulation/clear", headers={"X-CSRF-Token": "not-the-token"}, csrf=False)
    assert r.status_code == 403


def test_operators_cannot_use_admin_actions(operator, admin, db):
    r = operator.post("/api/simulation/scenarios/flood_conflict/load", json={"create_report": False})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    assert db.scalars(select(AuditEvent).where(AuditEvent.event_type == "auth.forbidden")).first() is not None
    assert admin.post("/api/simulation/scenarios/flood_conflict/load", json={"create_report": False}).status_code == 200


def test_no_web_endpoint_creates_operators(anon, operator):
    for url in ("/api/operators", "/api/auth/register", "/api/auth/signup", "/api/admin/operators"):
        assert anon.post(url, json={"username": "evil", "password": "x" * 20, "role": "admin"}).status_code in (401, 404, 405)
        assert operator.post(url, json={"username": "evil", "password": "x" * 20, "role": "admin"}).status_code in (404, 405)


# ---- provisioning CLI --------------------------------------------------------------------------
def _stdin(monkeypatch, text: str):
    monkeypatch.setattr("sys.stdin", io.StringIO(text + "\n"))


def test_cli_creates_first_admin_and_refuses_to_overwrite(db, monkeypatch, capsys):
    _stdin(monkeypatch, "Correct-Horse-Battery-9")
    assert cli(["operator", "create", "Chief.Admin", "--role", "admin", "--password-stdin"]) == 0
    op = db.scalars(select(Operator).where(Operator.username == "chief.admin")).one()
    original_hash = op.password_hash
    assert op.role == "admin" and op.password_hash.startswith("$argon2id$")
    _stdin(monkeypatch, "Another-Password-77x")
    assert cli(["operator", "create", "chief.admin", "--role", "operator", "--password-stdin"]) == 1
    db.expire_all()
    op = db.scalars(select(Operator).where(Operator.username == "chief.admin")).one()
    assert op.password_hash == original_hash and op.role == "admin", "existing account untouched"
    out = capsys.readouterr()
    assert "Correct-Horse" not in out.out + out.err and "argon2" not in out.out + out.err


def test_cli_rejects_weak_passwords_and_bad_usernames(db, monkeypatch):
    _stdin(monkeypatch, "short")
    assert cli(["operator", "create", "new.op", "--password-stdin"]) == 1
    _stdin(monkeypatch, "Good-Long-Password-1")
    assert cli(["operator", "create", "bad name!", "--password-stdin"]) == 1
    assert db.scalars(select(Operator)).first() is None


def test_cli_set_password_revokes_sessions(operator, db, monkeypatch):
    _stdin(monkeypatch, "Brand-New-Password-55")
    assert cli(["operator", "set-password", "ops.amara", "--password-stdin"]) == 0
    assert operator.get("/api/incidents").status_code == 401
    assert operator.login("ops.amara", "Brand-New-Password-55").status_code == 200


def test_cli_refuses_non_interactive_password_without_flag(db, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("secret-from-pipe-123\n"))
    with pytest.raises(SystemExit):
        cli(["operator", "create", "pipe.user"])

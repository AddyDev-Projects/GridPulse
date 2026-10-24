"""Test setup: an isolated PostgreSQL database, migrated with Alembic, truncated between tests.

The URL comes from GRIDPULSE_TEST_DATABASE_URL (environment or .env). resolve_test_database_url()
refuses databases not named *_test and the development database itself, so development data can
never be destroyed by the suite. Values below (secret key, passwords) are throwaway test fixtures.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.config import resolve_test_database_url

TEST_URL = resolve_test_database_url()
os.environ["GRIDPULSE_DATABASE_URL"] = TEST_URL
os.environ["GRIDPULSE_WORKFLOW_STEP_DELAY_MS"] = "0"
os.environ["GRIDPULSE_LLM_PROVIDER"] = "fallback"
os.environ["GRIDPULSE_SECRET_KEY"] = "test-only-secret-" + "x" * 40
os.environ["GRIDPULSE_COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import configure_engine, session_factory  # noqa: E402
from app.simulation.repository import ensure_initialized  # noqa: E402

get_settings.cache_clear()
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def alembic_config() -> Config:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    cfg.attributes["database_url"] = TEST_URL
    return cfg


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    # Rebuild the (guarded, *_test) database from an empty schema so leftovers from a previous run or a
    # manual session against it cannot break the reset.
    engine = configure_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


TABLES = (
    "audit_events, simulation_executions, simulation_routes, road_closures, approvals, plan_revisions, "
    "validation_findings, validation_runs, plan_actions, response_plans, agent_proposals, agent_runs, "
    "workflow_transitions, incidents, sensor_observations, infrastructure_edges, infrastructure_nodes, "
    "simulation_meta, road_graph_versions, cities, operator_sessions, operators, rate_limit_events"
)


def truncate_all(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def db(migrated_db):
    truncate_all(migrated_db)
    session = session_factory()()
    ensure_initialized(session)
    session.commit()
    yield session
    session.close()


class Api:
    """TestClient wrapper that remembers the CSRF token after login."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf: str | None = None

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, csrf=True, **kw):
        headers = kw.pop("headers", {})
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return self.c.post(url, json=json, headers=headers, **kw)

    def login(self, username: str, password: str):
        r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r


def make_operator(db, username: str, role: str = "operator") -> str:
    from app.services.auth import create_operator

    password = "Tst-" + secrets.token_urlsafe(18)
    create_operator(db, username, password, role, None, "test")
    return password


@pytest.fixture()
def app_client(db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def anon(db):
    from app.main import app

    with TestClient(app) as c:
        yield Api(c)


@pytest.fixture()
def operator(db):
    from app.main import app

    pw = make_operator(db, "ops.amara", "operator")
    with TestClient(app) as c:
        api = Api(c)
        assert api.login("ops.amara", pw).status_code == 200
        yield api


@pytest.fixture()
def admin(db):
    from app.main import app

    pw = make_operator(db, "admin.ravi", "admin")
    with TestClient(app) as c:
        api = Api(c)
        assert api.login("admin.ravi", pw).status_code == 200
        yield api


def fake_rows(sensor_specs, now=None):
    """Observation-like rows for pure (database-free) evidence tests."""
    now = now or datetime.now(timezone.utc)
    rows, i = [], 1
    for sensor_id, minutes_ago, value, quality in sensor_specs:
        rows.append(SimpleNamespace(id=i, sensor_id=sensor_id, measurement="water", value=value, unit="cm",
                                    observed_at=now - timedelta(minutes=minutes_ago), quality=quality, synthetic=True))
        i += 1
    return rows

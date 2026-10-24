"""Create the GridPulse PostgreSQL role(s) and databases. Cross-platform; never drops or resets anything.

Reads GRIDPULSE_DATABASE_URL and GRIDPULSE_TEST_DATABASE_URL (environment or .env) and, using a
PostgreSQL superuser connection, makes sure each role exists with LOGIN and each database exists
and is owned by its role. Existing roles, passwords and databases are left untouched.

Superuser connection, first match wins:
  --admin-url / GRIDPULSE_PG_ADMIN_URL      e.g. postgresql://postgres:secret@localhost:5432/postgres
  GRIDPULSE_PG_ADMIN_USER (default: postgres) + GRIDPULSE_PG_ADMIN_PASSWORD, on the app URL's host/port
  If the password is not set, connects without one (trust/peer auth, e.g. Homebrew) and, in an
  interactive terminal, prompts for it if the server asks for a password.

Usage (from backend/):
  python scripts/db_setup.py            create what is missing, then check both app connections
  python scripts/db_setup.py --check    only check that the app and test URLs can connect
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from psycopg import sql  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

from app.core.config import ENV_FILES, ConfigurationError, parse_postgres_url, resolve_test_database_url  # noqa: E402


def env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    for f in ENV_FILES:
        if f.exists():
            value = dotenv_values(f).get(name)
            if value:
                return value
    return None


def conn_kwargs(url: URL) -> dict:
    kwargs = {"host": url.host, "port": url.port or 5432, "user": url.username, "dbname": url.database or "postgres"}
    if url.password:
        kwargs["password"] = url.password
    return kwargs


def describe(url: URL) -> str:
    return f"{url.username}@{url.host}:{url.port or 5432}/{url.database}"


def admin_connection(app_url: URL, cli_admin_url: str | None) -> psycopg.Connection:
    raw = cli_admin_url or env("GRIDPULSE_PG_ADMIN_URL")
    if raw:
        admin = make_url(raw)
    else:
        password = env("GRIDPULSE_PG_ADMIN_PASSWORD")
        if password == "CHANGE_ME":
            raise SystemExit("GRIDPULSE_PG_ADMIN_PASSWORD in .env still has the placeholder value. Set the PostgreSQL "
                             "superuser password, or leave it empty for trust/peer authentication (e.g. Homebrew).")
        admin = URL.create("postgresql", username=env("GRIDPULSE_PG_ADMIN_USER") or "postgres",
                           password=password, host=app_url.host,
                           port=app_url.port or 5432, database="postgres")
    if not admin.database:
        admin = admin.set(database="postgres")
    try:
        return psycopg.connect(**conn_kwargs(admin), autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as exc:
        needs_password = "password" in str(exc).lower() and not admin.password
        if needs_password and sys.stdin.isatty():
            pw = getpass.getpass(f"Password for PostgreSQL superuser '{admin.username}': ")
            return psycopg.connect(**conn_kwargs(admin.set(password=pw)), autocommit=True, connect_timeout=10)
        raise SystemExit(
            f"Could not connect as PostgreSQL superuser {describe(admin)}: {first_line(exc)}\n"
            "Is PostgreSQL running? Set GRIDPULSE_PG_ADMIN_USER / GRIDPULSE_PG_ADMIN_PASSWORD in .env "
            "(or pass --admin-url). See README, 'Database setup'."
        ) from None


def first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def ensure(admin: psycopg.Connection, url: URL) -> None:
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (url.username,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(url.username), sql.Literal(url.password)))
            print(f"  created role {url.username}")
        else:
            print(f"  role {url.username} already exists (password not changed)")
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(url.database), sql.Identifier(url.username)))
            print(f"  created database {url.database}")
        else:
            print(f"  database {url.database} already exists (left unchanged)")


def check(label: str, url: URL) -> bool:
    try:
        with psycopg.connect(**conn_kwargs(url), connect_timeout=10) as c:
            version = c.execute("SHOW server_version").fetchone()[0]
        print(f"  OK  {label}: connected to {describe(url)} (PostgreSQL {version})")
        return True
    except psycopg.Error as exc:
        print(f"  FAIL {label}: {describe(url)}: {first_line(exc)}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only test connections; change nothing")
    parser.add_argument("--admin-url", help="superuser connection URL (overrides GRIDPULSE_PG_ADMIN_URL)")
    parser.add_argument("--skip-test-db", action="store_true", help="do not create or check the test database")
    args = parser.parse_args()

    try:
        dev_raw = env("GRIDPULSE_DATABASE_URL")
        if not dev_raw:
            raise ConfigurationError("GRIDPULSE_DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
        dev = parse_postgres_url(dev_raw)
        targets = [("development database", dev)]
        if not args.skip_test_db:
            targets.append(("test database", parse_postgres_url(resolve_test_database_url(), "GRIDPULSE_TEST_DATABASE_URL")))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if not args.check:
        print("Ensuring PostgreSQL roles and databases exist (nothing is dropped or reset):")
        with admin_connection(dev, args.admin_url) as admin:
            for _, url in targets:
                ensure(admin, url)

    print("Checking application connections:")
    ok = all([check(label, url) for label, url in targets])
    if not ok:
        print("\nAt least one connection failed. If the role already existed with a different password, either "
              "update the URL in .env or change the role's password in PostgreSQL.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

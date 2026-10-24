"""GridPulse administration CLI. Run from backend/ with the virtual environment's Python:

    python -m app.cli operator create <username> --role admin      # first administrator
    python -m app.cli operator create <username>                   # another operator
    python -m app.cli operator set-password <username>
    python -m app.cli operator disable <username>   |  enable <username>
    python -m app.cli operator set-role <username> <admin|operator>
    python -m app.cli operator revoke-sessions <username>
    python -m app.cli operator list
    python -m app.cli geodata info   |  geodata fetch
    python -m app.cli demo list      |  demo load <key> [--no-report]  |  demo clear

Passwords are read with getpass (not echoed) and typed twice, or one line from stdin with
--password-stdin for automation. They are never printed or logged. Commands never reset an
existing account implicitly: `create` refuses if the username exists.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select


def _actor() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "unknown"
    return f"cli:{user}"


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        line = sys.stdin.readline()
        if not line:
            raise SystemExit("No password on stdin.")
        return line.rstrip("\r\n")
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to read a password from a non-interactive stdin without --password-stdin.")
    first = getpass.getpass("New password (min 12 chars, not echoed): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match. Nothing was changed.")
    return first


def _db():
    from app.db.session import session_factory

    return session_factory()()


def cmd_operator(args: argparse.Namespace) -> int:
    from app.models import Operator
    from app.services import auth

    db = _db()
    try:
        if args.action == "list":
            rows = db.scalars(select(Operator).order_by(Operator.username)).all()
            if not rows:
                print("No operators. Create the first one with: python -m app.cli operator create <username> --role admin")
            for o in rows:
                last = o.last_login_at.isoformat(timespec="minutes") if o.last_login_at else "never"
                print(f"{o.username:<24} {o.role:<9} {'active' if o.is_active else 'DISABLED':<9} last login {last}")
            return 0
        if args.action == "create":
            password = _read_password(args)
            op = auth.create_operator(db, args.username, password, args.role, args.display_name, _actor())
            print(f"Created {op.role} '{op.username}'.")
        elif args.action == "set-password":
            password = _read_password(args)
            n = auth.set_password(db, args.username, password, _actor())
            print(f"Password updated for '{auth.normalize_username(args.username)}'; {n} session(s) revoked.")
        elif args.action in ("disable", "enable"):
            n = auth.set_active(db, args.username, args.action == "enable", _actor())
            print(f"'{auth.normalize_username(args.username)}' {args.action}d" + (f"; {n} session(s) revoked." if n else "."))
        elif args.action == "set-role":
            auth.set_role(db, args.username, args.role_value, _actor())
            print(f"'{auth.normalize_username(args.username)}' is now {args.role_value}; their sessions were revoked.")
        elif args.action == "revoke-sessions":
            op = db.scalars(select(Operator).where(Operator.username == auth.normalize_username(args.username))).first()
            if op is None:
                raise auth.ProvisioningError(f"No operator named '{args.username}'.")
            n = auth.revoke_all_sessions(db, op.id, "revoked via CLI")
            from app.services import audit

            audit.record(db, "auth.sessions_revoked", f"{n} session(s) of '{op.username}' revoked.", actor=_actor())
            db.commit()
            print(f"Revoked {n} session(s).")
        return 0
    except auth.ProvisioningError as exc:
        db.rollback()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_geodata(args: argparse.Namespace) -> int:
    from app.geo import osm
    from app.geo.road_graph import RoadGraph

    if args.action == "info":
        g = RoadGraph.load()
        for k in ("city", "source", "license", "osm_base_timestamp", "built_at", "node_count", "segment_count",
                  "signal_count", "poi_count", "sha256"):
            print(f"{k:<20} {g.meta.get(k)}")
        print(f"{'bbox':<20} {g.bbox}")
        return 0
    print("Fetching OpenStreetMap data from the Overpass API (this takes about a minute)...")
    data = osm.fetch_overpass(osm.MANGALURU_BBOX)
    meta = osm.write_graph_file(data, osm.MANGALURU_BBOX)
    print(f"Wrote {osm.GRAPH_FILE} ({meta['node_count']} nodes, {meta['segment_count']} segments, sha {meta['sha256'][:12]}).")
    print("Restart the backend to load it. A new graph version makes earlier plans stale.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from app.geo.road_graph import mangaluru_graph
    from app.services import audit
    from app.services.reports import create_report
    from app.simulation.repository import ScenarioError, clear_simulated_state, ensure_initialized, load_scenario, scenario_report
    from app.simulation.scenarios import SCENARIOS

    if args.action == "list":
        for s in SCENARIOS.values():
            print(f"{s.key:<20} {s.code}  {s.title}\n{'':<23}{s.expected_outcome}")
        return 0
    db = _db()
    try:
        ensure_initialized(db)
        if args.action == "clear":
            meta = clear_simulated_state(db, "Cleared via CLI")
            audit.record(db, "simulation.cleared", f"Simulated state cleared via CLI; city state v{meta.state_version}.",
                         actor=_actor())
            db.commit()
            print(f"Simulated state cleared (closures lifted, sensors offline). City state v{meta.state_version}.")
            return 0
        meta = load_scenario(db, args.key)
        sc = SCENARIOS[args.key]
        audit.record(db, "simulation.scenario_loaded", f"Demo scenario {sc.code} loaded via CLI (SIMULATED data).",
                     actor=_actor(), details={"scenario": sc.key, "epoch": meta.epoch})
        ref = None
        if not args.no_report:
            r = scenario_report(sc)
            inc = create_report(db, mangaluru_graph(), category=r["category"], description=r["description"],
                                lat=r["lat"], lon=r["lon"], indicators=r["indicators"],
                                location_description=r["location_description"], observed_at=None, source="demo_scenario")
            ref = inc.reference
        db.commit()
        print(f"Loaded scenario {sc.code}: {sc.title}. City state v{meta.state_version}."
              + (f" Simulated report {ref} is waiting in the operator queue." if ref else ""))
        return 0
    except ScenarioError as exc:
        db.rollback()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="GridPulse administration")
    sub = p.add_subparsers(dest="group", required=True)

    op = sub.add_parser("operator", help="manage operator accounts")
    op_sub = op.add_subparsers(dest="action", required=True)
    c = op_sub.add_parser("create", help="create an operator (refuses if the username exists)")
    c.add_argument("username")
    c.add_argument("--role", choices=("admin", "operator"), default="operator")
    c.add_argument("--display-name")
    c.add_argument("--password-stdin", action="store_true", help="read the password from one line of stdin")
    sp = op_sub.add_parser("set-password", help="reset a password and revoke the account's sessions")
    sp.add_argument("username")
    sp.add_argument("--password-stdin", action="store_true")
    for name in ("disable", "enable", "revoke-sessions"):
        op_sub.add_parser(name).add_argument("username")
    sr = op_sub.add_parser("set-role")
    sr.add_argument("username")
    sr.add_argument("role_value", choices=("admin", "operator"))
    op_sub.add_parser("list")

    gd = sub.add_parser("geodata", help="road-graph data")
    gd_sub = gd.add_subparsers(dest="action", required=True)
    gd_sub.add_parser("info")
    gd_sub.add_parser("fetch", help="re-download from OpenStreetMap and rebuild the graph file")

    dm = sub.add_parser("demo", help="explicit, labelled demo scenarios (simulated data)")
    dm_sub = dm.add_subparsers(dest="action", required=True)
    dm_sub.add_parser("list")
    ld = dm_sub.add_parser("load")
    ld.add_argument("key")
    ld.add_argument("--no-report", action="store_true", help="do not create the simulated demo report")
    dm_sub.add_parser("clear")

    args = p.parse_args(argv)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    return {"operator": cmd_operator, "geodata": cmd_geodata, "demo": cmd_demo}[args.group](args)


if __name__ == "__main__":
    raise SystemExit(main())

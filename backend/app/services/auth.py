"""Operator authentication, sessions and provisioning.

* Accounts are created only through the provisioning CLI (app/cli.py); no web endpoint creates them.
* Passwords are hashed with Argon2id; plaintext and hashes never reach logs or the audit trail.
* Sessions are opaque random tokens; only their SHA-256 is stored. Each session also has a CSRF
  token (double-submit: cookie + X-CSRF-Token header) checked on every state-changing request.
* Login failures are rate-limited per username and per client, with one generic error message.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    client_key,
    hash_password,
    needs_rehash,
    new_token,
    password_problems,
    token_hash,
    verify_password,
)
from app.db.base import utcnow
from app.models import Operator, OperatorSession
from app.services import audit, ratelimit

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
GENERIC_LOGIN_ERROR = "Invalid username or password."
LOGIN_WINDOW = timedelta(minutes=15)


@dataclass
class AuthError(Exception):
    status_code: int
    code: str
    message: str
    retry_after: int | None = None


@dataclass
class LoginResult:
    operator: Operator
    session_token: str
    csrf_token: str
    expires_at: object


def normalize_username(username: str) -> str:
    return username.strip().lower()


def login(db: Session, username: str, password: str, client_ip: str, user_agent: str | None) -> LoginResult:
    s = get_settings()
    uname = normalize_username(username)[:64]
    user_key = client_key("login-user", uname)
    ip_key = client_key("login-ip", client_ip)
    limit = s.login_failures_per_15min
    if (ratelimit.count(db, "login_fail_user", user_key, LOGIN_WINDOW) >= limit
            or ratelimit.count(db, "login_fail_ip", ip_key, LOGIN_WINDOW) >= limit * 4):
        retry = max(ratelimit.retry_after_s(db, "login_fail_user", user_key, LOGIN_WINDOW),
                    ratelimit.retry_after_s(db, "login_fail_ip", ip_key, LOGIN_WINDOW))
        audit.record(db, "auth.login_throttled", "Login attempt refused: too many recent failures.",
                     actor="anonymous", severity="warning", details={"account_key": user_key[:12]})
        db.commit()
        raise AuthError(429, "too_many_attempts", "Too many failed sign-in attempts. Try again later.", retry)

    op = db.scalars(select(Operator).where(Operator.username == uname)).first()
    ok = verify_password(op.password_hash if op else None, password)  # always runs Argon2 (timing)
    if not ok or op is None or not op.is_active:
        ratelimit.record(db, "login_fail_user", user_key)
        ratelimit.record(db, "login_fail_ip", ip_key)
        audit.record(db, "auth.login_failed", "Operator sign-in failed.", actor="anonymous", severity="warning",
                     details={"account_key": user_key[:12],
                              "reason": "inactive" if (op is not None and ok) else "bad_credentials"})
        db.commit()
        raise AuthError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)

    if needs_rehash(op.password_hash):
        op.password_hash = hash_password(password)
    now = utcnow()
    token, csrf = new_token(), new_token()
    session = OperatorSession(token_hash=token_hash(token), csrf_hash=token_hash(csrf), operator_id=op.id,
                              expires_at=now + timedelta(hours=s.session_hours),
                              idle_expires_at=now + timedelta(minutes=s.session_idle_minutes),
                              user_agent=(user_agent or "")[:200] or None)
    db.add(session)
    op.last_login_at = now
    audit.record(db, "auth.login_succeeded", f"Operator {op.username} signed in.", actor=f"operator:{op.username}",
                 severity="info", details={"role": op.role})
    ratelimit.prune(db)
    db.commit()
    return LoginResult(op, token, csrf, session.expires_at)


def resolve_session(db: Session, token: str | None) -> tuple[Operator, OperatorSession] | None:
    if not token or len(token) > 200:
        return None
    sess = db.scalars(select(OperatorSession).where(OperatorSession.token_hash == token_hash(token))).first()
    now = utcnow()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now or sess.idle_expires_at <= now:
        return None
    op = db.get(Operator, sess.operator_id)
    if op is None or not op.is_active:
        return None
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if sess.idle_expires_at - now < idle - timedelta(minutes=1):  # slide, at most once a minute
        sess.idle_expires_at = min(now + idle, sess.expires_at)
        db.commit()
    return op, sess


def logout(db: Session, sess: OperatorSession, op: Operator) -> None:
    sess.revoked_at, sess.revoked_reason = utcnow(), "logout"
    audit.record(db, "auth.logout", f"Operator {op.username} signed out.", actor=f"operator:{op.username}")
    db.commit()


def revoke_all_sessions(db: Session, operator_id: uuid.UUID, reason: str) -> int:
    res = db.execute(update(OperatorSession).where(OperatorSession.operator_id == operator_id,
                                                   OperatorSession.revoked_at.is_(None))
                     .values(revoked_at=utcnow(), revoked_reason=reason[:80]))
    return res.rowcount or 0


# ---- provisioning (used by the CLI) ------------------------------------------------------------
class ProvisioningError(ValueError):
    pass


def validate_new_credentials(username: str, password: str) -> str:
    uname = normalize_username(username)
    if not USERNAME_RE.fullmatch(uname):
        raise ProvisioningError("Username must be 3-40 characters: lowercase letters, digits, '.', '_' or '-', "
                                "starting with a letter or digit.")
    problems = password_problems(uname, password)
    if problems:
        raise ProvisioningError("Password " + "; ".join(problems) + ".")
    return uname


def create_operator(db: Session, username: str, password: str, role: str, display_name: str | None, actor: str) -> Operator:
    if role not in ("admin", "operator"):
        raise ProvisioningError("Role must be 'admin' or 'operator'.")
    uname = validate_new_credentials(username, password)
    if db.scalars(select(Operator).where(Operator.username == uname)).first() is not None:
        raise ProvisioningError(f"Operator '{uname}' already exists. Nothing was changed "
                                "(use set-password or set-role to modify it).")
    op = Operator(username=uname, display_name=(display_name or uname)[:80], password_hash=hash_password(password),
                  role=role, is_active=True)
    db.add(op)
    db.flush()
    audit.record(db, "auth.operator_created", f"Operator account '{uname}' created with role {role}.", actor=actor,
                 details={"username": uname, "role": role})
    db.commit()
    return op


def _get(db: Session, username: str) -> Operator:
    op = db.scalars(select(Operator).where(Operator.username == normalize_username(username))).first()
    if op is None:
        raise ProvisioningError(f"No operator named '{normalize_username(username)}'.")
    return op


def set_password(db: Session, username: str, password: str, actor: str) -> int:
    op = _get(db, username)
    validate_new_credentials(op.username, password)
    op.password_hash = hash_password(password)
    op.password_changed_at = utcnow()
    revoked = revoke_all_sessions(db, op.id, "password changed")
    audit.record(db, "auth.password_changed", f"Password for '{op.username}' was reset; {revoked} session(s) revoked.",
                 actor=actor, details={"username": op.username})
    db.commit()
    return revoked


def set_active(db: Session, username: str, active: bool, actor: str) -> int:
    op = _get(db, username)
    op.is_active = active
    revoked = 0 if active else revoke_all_sessions(db, op.id, "account disabled")
    audit.record(db, "auth.operator_enabled" if active else "auth.operator_disabled",
                 f"Operator '{op.username}' {'enabled' if active else 'disabled'}"
                 + ("" if active else f"; {revoked} session(s) revoked") + ".", actor=actor,
                 severity="info" if active else "warning", details={"username": op.username})
    db.commit()
    return revoked


def set_role(db: Session, username: str, role: str, actor: str) -> None:
    if role not in ("admin", "operator"):
        raise ProvisioningError("Role must be 'admin' or 'operator'.")
    op = _get(db, username)
    old = op.role
    op.role = role
    revoke_all_sessions(db, op.id, "role changed")
    audit.record(db, "auth.role_changed", f"Operator '{op.username}' role {old} → {role}.", actor=actor,
                 details={"username": op.username, "from": old, "to": role})
    db.commit()

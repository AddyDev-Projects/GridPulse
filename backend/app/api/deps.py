"""Request dependencies that enforce authentication, roles and CSRF on the server side.

Every operator endpoint depends on `require_operator` (or `require_admin`). The frontend hiding a
button is a convenience only; these checks are what actually protect the API.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import tokens_match
from app.db.session import get_db
from app.models import Operator, OperatorSession
from app.services import audit
from app.services.auth import resolve_session

SESSION_COOKIE = "gp_session"
CSRF_COOKIE = "gp_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    operator: Operator
    session: OperatorSession


def client_ip(request: Request) -> str:
    # Direct peer address. X-Forwarded-For is deliberately not trusted (no proxy is configured).
    return request.client.host if request.client else "unknown"


def require_operator(request: Request, db: Session = Depends(get_db)) -> Principal:
    resolved = resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(401, detail={"code": "not_authenticated",
                                         "message": "Operator sign-in required (session missing or expired)."})
    op, sess = resolved
    if request.method not in SAFE_METHODS and not tokens_match(request.headers.get(CSRF_HEADER), sess.csrf_hash):
        audit.record(db, "auth.csrf_rejected", f"State-changing request without a valid CSRF token from {op.username}.",
                     actor=f"operator:{op.username}", severity="warning", details={"path": request.url.path})
        db.commit()
        raise HTTPException(403, detail={"code": "csrf_failed",
                                         "message": "Missing or invalid CSRF token. Reload the console and try again."})
    return Principal(op, sess)


def require_admin(request: Request, principal: Principal = Depends(require_operator),
                  db: Session = Depends(get_db)) -> Principal:
    if principal.operator.role != "admin":
        audit.record(db, "auth.forbidden", f"{principal.operator.username} attempted an admin-only action.",
                     actor=f"operator:{principal.operator.username}", severity="warning",
                     details={"path": request.url.path})
        db.commit()
        raise HTTPException(403, detail={"code": "forbidden", "message": "This action requires the admin role."})
    return principal

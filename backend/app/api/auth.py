from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, Principal, client_ip, require_operator
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.api import LoginRequest, LoginResponse, OperatorOut
from app.services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _operator_out(op) -> OperatorOut:
    return OperatorOut(username=op.username, display_name=op.display_name, role=op.role)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        result = auth.login(db, body.username, body.password, client_ip(request), request.headers.get("user-agent"))
    except auth.AuthError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}, headers=headers) from None
    s = get_settings()
    max_age = int(s.session_hours * 3600)
    response.set_cookie(SESSION_COOKIE, result.session_token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="strict", path="/api")
    response.set_cookie(CSRF_COOKIE, result.csrf_token, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="strict", path="/")
    return LoginResponse(operator=_operator_out(result.operator), csrf_token=result.csrf_token,
                         expires_at=result.expires_at)


@router.post("/logout", status_code=204)
def logout(response: Response, principal: Principal = Depends(require_operator), db: Session = Depends(get_db)) -> Response:
    auth.logout(db, principal.session, principal.operator)
    response.delete_cookie(SESSION_COOKIE, path="/api")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=OperatorOut)
def me(principal: Principal = Depends(require_operator)) -> OperatorOut:
    return _operator_out(principal.operator)

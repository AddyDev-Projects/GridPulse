import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api import auth as auth_api
from app.api import public as public_api
from app.api.routes import health_router, router
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.session import session_factory
from app.geo.road_graph import mangaluru_graph
from app.models import Incident
from app.services import audit
from app.simulation.repository import ScenarioError, SimulationNotInitialized, ensure_initialized

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gridpulse")

TERMINAL = ("EXECUTED", "REJECTED", "ESCALATED", "NO_SAFE_PLAN", "FAILED", "AWAITING_APPROVAL", "APPROVED", "DISMISSED")


def startup_checks() -> None:
    """Load the road graph (fails fast if missing/corrupt), register reference data, recover runs.

    Registers only the City, the road-graph version and the simulated infrastructure overlay.
    It never creates incidents, sensors or closures.
    """
    graph = mangaluru_graph()
    db = session_factory()()
    try:
        ensure_initialized(db, graph)
        stuck = db.scalars(select(Incident).where(Incident.run_started_at.is_not(None),
                                                  Incident.run_finished_at.is_(None),
                                                  Incident.status.not_in(TERMINAL))).all()
        for inc in stuck:
            audit.transition(db, inc, "FAILED", "Workflow interrupted by a server restart")
            inc.run_finished_at = utcnow()
            audit.record(db, "workflow.failed", "Workflow interrupted by a server restart; no actions were taken.",
                         incident_id=inc.id, actor="system", severity="error")
        db.commit()
    finally:
        db.close()
    log.info("Road graph loaded: Mangaluru, %d nodes, %d segments (OSM, %s).", len(graph.nodes), len(graph.segments),
             graph.meta.get("osm_base_timestamp"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_checks()
    log.info("GridPulse ready (LLM provider: %s). SIMULATION ONLY: nothing is dispatched in the real world.",
             get_settings().llm_provider)
    yield


class BodySizeLimit:
    """Reject request bodies over the configured size (by header or while streaming)."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            await _too_large(send)
            return
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TooLarge:
            await _too_large(send)


class _TooLarge(Exception):
    pass


async def _too_large(send: Send) -> None:
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body",
                "body": b'{"detail":{"code":"payload_too_large","message":"Request body too large."}}'})


app = FastAPI(
    title="GridPulse API",
    version="0.2.0",
    description="Disaster-response coordination SIMULATION on the real Mangaluru road graph (OpenStreetMap). "
                "Sensors, power network and executions are simulated.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.add_middleware(BodySizeLimit, max_bytes=get_settings().max_request_bytes)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(health_router)
app.include_router(auth_api.router)
app.include_router(public_api.router)
app.include_router(router)


@app.exception_handler(SimulationNotInitialized)
async def _not_initialized(_: Request, exc: SimulationNotInitialized) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": {"code": "not_initialized", "message": str(exc)}})


@app.exception_handler(ScenarioError)
async def _scenario(_: Request, exc: ScenarioError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": {"code": "scenario_error", "message": str(exc)}})


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "Invalid request.",
                                                             "errors": errors}})


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": {"code": "internal_error",
                                                             "message": "Internal error; the operation did not complete."}})

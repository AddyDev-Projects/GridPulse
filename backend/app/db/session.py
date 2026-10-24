from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def configure_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    # Sessions run in UTC so every timestamptz comes back (and is serialised by the API) as UTC, whatever
    # the server's or the database's local time zone. Stored values are absolute instants either way.
    _engine = create_engine(url or get_settings().database_url, pool_pre_ping=True, future=True,
                            connect_args={"options": "-c timezone=UTC"})
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_engine()
    assert _engine is not None
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        configure_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()

"""Operator accounts, server-side sessions, and rate-limit events."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow

ROLES = ("admin", "operator")


class Operator(Base, TimestampMixin):
    """An authenticated operator. Created only by the provisioning CLI; never via the web API."""

    __tablename__ = "operators"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="role"),
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("char_length(username) BETWEEN 3 AND 40", name="username_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # Argon2id
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class OperatorSession(Base, TimestampMixin):
    """Server-side session. Only SHA-256 hashes of the session and CSRF tokens are stored."""

    __tablename__ = "operator_sessions"
    __table_args__ = (Index("ix_operator_sessions_operator", "operator_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(200))


class RateLimitEvent(Base):
    """One row per counted event (report submission, failed login). Keys are salted hashes, never raw IPs."""

    __tablename__ = "rate_limit_events"
    __table_args__ = (Index("ix_rate_limit_events_lookup", "bucket", "key_hash", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                                                 default=utcnow)

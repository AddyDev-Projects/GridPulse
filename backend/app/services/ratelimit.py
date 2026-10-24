"""Database-backed sliding-window rate limits. Works across processes; keys are HMACs, not raw IPs."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import RateLimitEvent


def count(db: Session, bucket: str, key_hash: str, window: timedelta) -> int:
    since = utcnow() - window
    return db.scalar(select(func.count()).select_from(RateLimitEvent).where(
        RateLimitEvent.bucket == bucket, RateLimitEvent.key_hash == key_hash, RateLimitEvent.created_at >= since)) or 0


def record(db: Session, bucket: str, key_hash: str) -> None:
    db.add(RateLimitEvent(bucket=bucket, key_hash=key_hash))


def retry_after_s(db: Session, bucket: str, key_hash: str, window: timedelta) -> int:
    oldest = db.scalar(select(func.min(RateLimitEvent.created_at)).where(
        RateLimitEvent.bucket == bucket, RateLimitEvent.key_hash == key_hash,
        RateLimitEvent.created_at >= utcnow() - window))
    if oldest is None:
        return 0
    return max(1, int((oldest + window - utcnow()).total_seconds()))


def prune(db: Session, older_than: timedelta = timedelta(days=2)) -> None:
    """Rate-limit counters are transient operational data, not records; old ones are discarded."""
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < utcnow() - older_than))

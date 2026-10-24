"""Persisted city state: the City entity, road-graph versions, road closures, the simulated
infrastructure overlay, synthetic sensors, designated routes and simulated executions.

Datasets in infrastructure_nodes / infrastructure_edges:
  * "mangaluru_sim"        the current overlay on the real Mangaluru road graph (simulated power model)
  * "legacy_meridian_bay"  rows from the earlier fictional city, kept read-only for history
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow

NODE_KINDS = ("intersection", "signal", "substation", "sensor", "pump")
EDGE_KINDS = ("road", "controls", "power_feed")
DATASET_CURRENT = "mangaluru_sim"
DATASET_LEGACY = "legacy_meridian_bay"


class City(Base, TimestampMixin):
    """The city GridPulse covers. Incidents, graph versions and closures belong to a city."""

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_bbox: Mapped[list[float]] = mapped_column(JSONB, nullable=False)  # [south, west, north, east]


class RoadGraphVersion(Base):
    """A loaded road-graph file (OpenStreetMap extract). Exactly one active version per city."""

    __tablename__ = "road_graph_versions"
    __table_args__ = (
        Index("uq_road_graph_versions_active", "city_id", unique=True, postgresql_where=text("active")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    license: Mapped[str] = mapped_column(String(200), nullable=False)
    osm_base_timestamp: Mapped[str | None] = mapped_column(String(40))
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    poi_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class RoadClosure(Base, TimestampMixin):
    """Simulated closure or caution on a real road segment. Rows are lifted, never deleted."""

    __tablename__ = "road_closures"
    __table_args__ = (
        CheckConstraint("kind IN ('closed', 'caution')", name="kind"),
        CheckConstraint("source IN ('plan_execution', 'scenario')", name="source"),
        Index("uq_road_closures_active_segment", "city_id", "segment_id", unique=True,
              postgresql_where=text("lifted_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("response_plans.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(Text)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifted_reason: Mapped[str | None] = mapped_column(Text)


class SimulationMeta(Base):
    """Single-row table. state_version increments on every mutation of simulated city state."""

    __tablename__ = "simulation_meta"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class InfrastructureNode(Base):
    __tablename__ = "infrastructure_nodes"
    __table_args__ = (
        CheckConstraint(f"kind IN {NODE_KINDS}", name="kind"),
        Index("ix_infrastructure_nodes_kind", "kind"),
        Index("ix_infrastructure_nodes_dataset", "dataset"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    zone: Mapped[str] = mapped_column(String(32), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)  # legacy layout x; longitude for mangaluru_sim
    y: Mapped[float] = mapped_column(Float, nullable=False)  # legacy layout y; latitude for mangaluru_sim
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False, default=DATASET_LEGACY,
                                         server_default=DATASET_LEGACY)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class InfrastructureEdge(Base):
    __tablename__ = "infrastructure_edges"
    __table_args__ = (
        CheckConstraint(f"kind IN {EDGE_KINDS}", name="kind"),
        CheckConstraint("source_id <> target_id", name="no_self_loop"),
        Index("ix_infrastructure_edges_kind", "kind"),
        Index("ix_infrastructure_edges_source", "source_id"),
        Index("ix_infrastructure_edges_target", "target_id"),
        Index("ix_infrastructure_edges_dataset", "dataset"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("infrastructure_nodes.id", ondelete="RESTRICT"), nullable=False)
    target_id: Mapped[str] = mapped_column(ForeignKey("infrastructure_nodes.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    length_m: Mapped[float | None] = mapped_column(Float)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False, default=DATASET_LEGACY,
                                         server_default=DATASET_LEGACY)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class SensorObservation(Base):
    """Synthetic sensor readings. Every row is simulated; `synthetic` is enforced true."""

    __tablename__ = "sensor_observations"
    __table_args__ = (
        CheckConstraint("synthetic = true", name="synthetic_only"),
        Index("ix_sensor_observations_lookup", "sensor_id", "epoch", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("infrastructure_nodes.id", ondelete="RESTRICT"), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    measurement: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SimulationRoute(Base, TimestampMixin):
    """Routes designated by executed plans (simulation only). Node ids are road-graph node ids."""

    __tablename__ = "simulation_routes"
    __table_args__ = (Index("ix_simulation_routes_active", "epoch", "active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    origin_id: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    node_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SimulationExecution(Base):
    """One row per successfully executed plan. UNIQUE(plan_id) blocks duplicate execution."""

    __tablename__ = "simulation_executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("response_plans.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("approvals.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    state_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    executed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    executed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id", ondelete="RESTRICT"))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)

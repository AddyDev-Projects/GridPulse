"""Explicit allowlist of simulated actions. Anything not listed here can never be validated or executed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(_Strict):
    reason: str | None = Field(default=None, max_length=300)


class SegmentsParams(_Strict):
    segment_ids: list[str] = Field(min_length=1, max_length=250)
    radius_m: float | None = Field(default=None, ge=0, le=2000)
    reason: str | None = Field(default=None, max_length=300)


class RouteParams(_Strict):
    purpose: Literal["emergency_access", "medical_transfer"]
    name: str = Field(max_length=160)
    origin: str = Field(max_length=32)
    origin_label: str = Field(max_length=200)
    destination: str = Field(max_length=32)
    destination_label: str = Field(max_length=200)
    edge_path: list[str] = Field(min_length=1, max_length=2000)
    node_path: list[str] = Field(min_length=2, max_length=2001)
    length_m: float = Field(gt=0)
    travel_s: float = Field(gt=0)


class TransferParams(_Strict):
    from_feed: str = Field(max_length=64)
    to_feed: str = Field(max_length=64)


class AdvisoryParams(_Strict):
    message: str = Field(min_length=5, max_length=280)


class VerificationParams(_Strict):
    question: str = Field(min_length=5, max_length=280)


@dataclass(frozen=True)
class ActionType:
    name: str
    label: str
    target_kind: str  # "road" | "substation" | "signal" | "intersection"
    impact: str
    params_model: type[BaseModel]
    owner: str  # agent responsible for proposing it


ACTION_TYPES: dict[str, ActionType] = {
    a.name: a
    for a in [
        ActionType("close_roads", "Close road segments", "road", "high", SegmentsParams, "mobility"),
        ActionType("set_roads_caution", "Set road segments to caution", "road", "low", SegmentsParams, "mobility"),
        ActionType("designate_response_route", "Designate response route", "intersection", "high", RouteParams, "mobility"),
        ActionType("isolate_substation", "Isolate simulated feeder zone", "substation", "high", NoParams, "grid"),
        ActionType("transfer_signal_power", "Transfer signal to backup feed (simulated)", "signal", "high", TransferParams, "grid"),
        ActionType("issue_public_advisory", "Issue public advisory (simulated)", "road", "low", AdvisoryParams, "coordinator"),
        ActionType("dispatch_field_verification", "Request field verification (simulated)", "road", "low", VerificationParams, "sentinel"),
    ]
}

ROUTE_ACTIONS = ("designate_response_route",)


def validate_params(action_type: str, params: dict[str, Any]) -> str | None:
    """Return an error message, or None if params match the action's schema."""
    spec = ACTION_TYPES.get(action_type)
    if spec is None:
        return f"Unknown action type '{action_type}'"
    try:
        spec.params_model.model_validate(params)
    except ValidationError as exc:
        return "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'params'}: {err['msg']}" for err in exc.errors())
    return None

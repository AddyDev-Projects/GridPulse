"""Incident check constraints for the citizen-report workflow.

Autogenerate does not detect CHECK changes, so these are written by hand:
  * status may now be DISMISSED (operator closes a report without running agents)
  * source is one of citizen_portal / demo_scenario / legacy
  * coordinates, when present, are valid latitude/longitude
Existing rows already satisfy all three (verified by the constraint creation itself).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("REPORTED", "TRIAGED", "EVIDENCE_GATHERED", "PLANS_PROPOSED", "VALIDATING", "REVISION_REQUIRED",
            "VALIDATING_AGAIN", "AWAITING_APPROVAL", "APPROVED", "EXECUTED", "REJECTED", "ESCALATED", "NO_SAFE_PLAN",
            "FAILED")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_incidents_status"), "incidents", type_="check")
    op.create_check_constraint(op.f("ck_incidents_status"), "incidents", f"status IN {STATUSES + ('DISMISSED',)}")
    op.create_check_constraint(op.f("ck_incidents_source"), "incidents",
                               "source IN ('citizen_portal', 'demo_scenario', 'legacy')")
    op.create_check_constraint(op.f("ck_incidents_coordinates"), "incidents",
                               "lat IS NULL OR (lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180)")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_incidents_coordinates"), "incidents", type_="check")
    op.drop_constraint(op.f("ck_incidents_source"), "incidents", type_="check")
    op.drop_constraint(op.f("ck_incidents_status"), "incidents", type_="check")
    op.create_check_constraint(op.f("ck_incidents_status"), "incidents", f"status IN {STATUSES}")

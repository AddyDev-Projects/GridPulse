"""Operators can resolve an emergency: RESOLVED status plus who resolved it, when, and why.

Additive: three nullable columns and a wider status CHECK. Existing rows are untouched and already
satisfy the new constraint. Downgrade refuses while any incident is RESOLVED, rather than rewrite it.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("REPORTED", "TRIAGED", "EVIDENCE_GATHERED", "PLANS_PROPOSED", "VALIDATING", "REVISION_REQUIRED",
            "VALIDATING_AGAIN", "AWAITING_APPROVAL", "APPROVED", "EXECUTED", "REJECTED", "ESCALATED", "NO_SAFE_PLAN",
            "FAILED", "DISMISSED")


def upgrade() -> None:
    op.add_column("incidents", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incidents", sa.Column("resolved_by_id", sa.Uuid(), nullable=True))
    op.add_column("incidents", sa.Column("resolution_note", sa.String(length=500), nullable=True))
    op.create_foreign_key(op.f("fk_incidents_resolved_by_id_operators"), "incidents", "operators",
                          ["resolved_by_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint(op.f("ck_incidents_status"), "incidents", type_="check")
    op.create_check_constraint(op.f("ck_incidents_status"), "incidents", f"status IN {STATUSES + ('RESOLVED',)}")


def downgrade() -> None:
    resolved = op.get_bind().execute(sa.text("SELECT count(*) FROM incidents WHERE status = 'RESOLVED'")).scalar()
    if resolved:
        raise RuntimeError(f"Cannot downgrade below 0005: {resolved} incident(s) are RESOLVED. Nothing was changed. "
                           "Restore a backup taken before upgrading instead.")
    op.drop_constraint(op.f("ck_incidents_status"), "incidents", type_="check")
    op.create_check_constraint(op.f("ck_incidents_status"), "incidents", f"status IN {STATUSES}")
    op.drop_constraint(op.f("fk_incidents_resolved_by_id_operators"), "incidents", type_="foreignkey")
    op.drop_column("incidents", "resolution_note")
    op.drop_column("incidents", "resolved_by_id")
    op.drop_column("incidents", "resolved_at")

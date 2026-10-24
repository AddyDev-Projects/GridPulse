"""Index public incident reports for the citizen recent-reports feed.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_incidents_public_created",
        "incidents",
        ["source", "city_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_public_created", table_name="incidents")

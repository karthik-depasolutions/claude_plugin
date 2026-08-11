"""initial - runs table

Revision ID: 0001
Revises:
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False, index=True),
        sa.Column("current_stage", sa.String(length=32), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("output_dir", sa.Text(), nullable=False),
        sa.Column("industry_override", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("record_json", sa.JSON(), nullable=False),
        sa.Column("binding_overrides_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("runs")

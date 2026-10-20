"""warehouse schema lifecycle columns on runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("warehouse_schema", sa.String(length=128), nullable=True))
    op.add_column("runs", sa.Column("warehouse_provisioned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_runs_warehouse_schema", "runs", ["warehouse_schema"])


def downgrade() -> None:
    op.drop_index("ix_runs_warehouse_schema", table_name="runs")
    op.drop_column("runs", "warehouse_provisioned_at")
    op.drop_column("runs", "warehouse_schema")

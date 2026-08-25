"""Add tenant_id to runs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25

All existing rows get tenant_id='_local' (the server_default) which preserves
existing behaviour — local dev runs remain visible to all users of the same
server as before this migration; only new runs created by an authenticated user
get that user's email as their tenant_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("tenant_id", sa.String(length=255), nullable=False, server_default="_local"),
    )
    op.create_index("ix_runs_tenant_id", "runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_tenant_id", table_name="runs")
    op.drop_column("runs", "tenant_id")

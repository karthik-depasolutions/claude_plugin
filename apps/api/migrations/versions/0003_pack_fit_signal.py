"""pack-fit demand signal columns on runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02
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
    op.add_column("runs", sa.Column("primary_pack_slug", sa.String(length=64), nullable=True))
    op.add_column("runs", sa.Column("pack_confidence", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("kpis_compiled", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("kpis_skipped", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_runs_primary_pack_slug", "runs", ["primary_pack_slug"])


def downgrade() -> None:
    op.drop_index("ix_runs_primary_pack_slug", table_name="runs")
    for name in ("kpis_skipped", "kpis_compiled", "pack_confidence", "primary_pack_slug"):
        op.drop_column("runs", name)

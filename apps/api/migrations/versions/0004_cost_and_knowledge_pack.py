"""run cost estimate + knowledge-pack coverage columns on runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("llm_estimated_cost_usd", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("cookbook_pass_rate", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("schema_coverage", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("schema_model_cache_hit", sa.Boolean(), nullable=True))


def downgrade() -> None:
    for name in (
        "schema_model_cache_hit",
        "schema_coverage",
        "cookbook_pass_rate",
        "llm_estimated_cost_usd",
    ):
        op.drop_column("runs", name)

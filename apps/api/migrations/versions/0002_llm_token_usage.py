"""llm token usage columns on runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_INT_COLS = ("llm_input_tokens", "llm_output_tokens", "llm_total_tokens", "llm_calls")


def upgrade() -> None:
    for name in _INT_COLS:
        op.add_column("runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("llm_token_usage_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "llm_token_usage_json")
    for name in reversed(_INT_COLS):
        op.drop_column("runs", name)

"""Add use_llm and use_agent flags to runs table

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25

These flags are carried across pause/resume so a run started with --no-llm
or use_agent=False never silently flips to LLM/agent mode on the second pass
through the orchestrator (previously they were in-memory only and lost on
API restart, causing the bug described in issue #147).
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
    op.add_column(
        "runs",
        sa.Column("use_llm", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "runs",
        sa.Column("use_agent", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("runs", "use_agent")
    op.drop_column("runs", "use_llm")

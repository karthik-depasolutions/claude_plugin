"""Add token usage accounting columns to runs table

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25

"How many tokens did this plugin cost to build?" needs to be answerable per
run and aggregatable per tenant, so the totals are promoted out of the
record_json blob into real columns. The per-component breakdown (profiling
vs generation vs the agents) stays in record_json["token_usage"], which is
detail rather than something anyone queries across runs.

Existing rows default to 0: they were built before any usage was recorded,
and 0 is honest for them - the alternative (NULL) would force every consumer
to special-case a distinction nobody can act on.
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
    for column in ("input_tokens", "output_tokens", "total_tokens", "llm_calls"):
        op.add_column("runs", sa.Column(column, sa.Integer(), nullable=False, server_default="0"))
    # Indexed because the natural queries are "spend over time" and
    # "most expensive runs", both of which sort/filter on the total.
    op.create_index("ix_runs_total_tokens", "runs", ["total_tokens"])


def downgrade() -> None:
    op.drop_index("ix_runs_total_tokens", table_name="runs")
    for column in ("llm_calls", "total_tokens", "output_tokens", "input_tokens"):
        op.drop_column("runs", column)

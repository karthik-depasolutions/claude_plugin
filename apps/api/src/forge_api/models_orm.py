"""The `runs` table — durable storage for `RunRecord`. The in-memory registry
(`forge_api.registry`) is the source of truth while a run's pipeline thread is
alive; every stage transition is mirrored here so runs survive a restart and
`GET /runs` can list history beyond process lifetime."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge_api.db import Base


class RunORM(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_path: Mapped[str] = mapped_column(Text)
    output_dir: Mapped[str] = mapped_column(Text)
    industry_override: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_json: Mapped[dict] = mapped_column(JSON)
    binding_overrides_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class UserORM(Base):
    """Admin-provisioned accounts - see scripts/create_user.py. Email is the
    natural key; there's no separate id column since nothing else needs one."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

"""The `runs` table — durable storage for `RunRecord`. The in-memory registry
(`forge_api.registry`) is the source of truth while a run's pipeline thread is
alive; every stage transition is mirrored here so runs survive a restart and
`GET /runs` can list history beyond process lifetime."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
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
    # LLM token usage for the whole generation run (see forge_core.llm.usage).
    # `_json` holds the by-model / by-role breakdown behind the scalars.
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Estimated run cost at LLM list prices (forge_core.llm.usage._PRICE_PER_MTOK).
    llm_estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Knowledge-pack (schema_model.json) health: verified cookbook fraction,
    # fraction of columns with a specific meaning, and whether synthesis was
    # served from the on-disk cache (a re-run of the same schema).
    cookbook_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    schema_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    schema_model_cache_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Pack-fit demand signal: which vertical this run landed on, how confident
    # the classifier was, and how many KPIs it could actually compile. Lets
    # `GET /runs` aggregate "which pack to build next" from real usage - a run
    # on `generic-analytics` or with many skipped KPIs is unmet demand.
    primary_pack_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pack_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    kpis_compiled: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    kpis_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The `client_<label>_<run_id>` warehouse schema backing this run, when its
    # data was loaded into (upload) or cloned into (postgresql:// + clone) the
    # Forge warehouse - NULL for a live-DB or bundled-file run. A bare
    # identifier, not a credential: DELETE /runs and scripts/ops/warehouse_gc.py
    # use it to reclaim the schema + its scoped role.
    warehouse_schema: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    warehouse_provisioned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

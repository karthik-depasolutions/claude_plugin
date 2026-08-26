"""Temporal workflow definition for durable, production-grade Data2plugin generation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from temporalio import workflow
from temporalio.common import RetryPolicy

# Import activities within workflow definition types
with workflow.unsafe.imports_passed_through():
    from workers.temporal_worker.activities.activities import (
        GraphActivityInput,
        GraphActivityOutput,
        IngestActivityInput,
        IngestActivityOutput,
        PackageActivityInput,
        PackageActivityOutput,
        ProfileActivityInput,
        ProfileActivityOutput,
        ValidateActivityInput,
        ValidateActivityOutput,
        ingest_activity,
        package_activity,
        profile_activity,
        run_forge_graph_activity,
        validate_activity,
    )

TASK_QUEUE = "data2plugin-generation"


class ForgeWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str = "default"
    source_path: str
    output_dir: str
    industry_override: str | None = None
    data_answers: dict[str, str] = {}
    binding_confirmations: dict[str, str | None] = {}
    use_agent: bool = True
    label: str | None = None


class ForgeWorkflowOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    plugin_dir: str | None = None
    error: str | None = None
    validation_status: str | None = None


@workflow.defn
class ForgeGenerationWorkflow:
    """Durable workflow executing end-to-end plugin generation with LangGraph and activity retries."""

    def __init__(self) -> None:
        self._current_stage: str = "initialized"
        self._status: str = "running"
        self._plugin_dir: str | None = None
        self._error: str | None = None

    @workflow.query
    def current_stage(self) -> str:
        return self._current_stage

    @workflow.query
    def status(self) -> str:
        return self._status

    @workflow.run
    async def run(self, input_data: ForgeWorkflowInput) -> ForgeWorkflowOutput:
        standard_retry = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=3,
        )

        try:
            # 1. Ingestion Activity
            self._current_stage = "ingest"
            ingest_res: IngestActivityOutput = await workflow.execute_activity(
                ingest_activity,
                IngestActivityInput(source_path=input_data.source_path),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=standard_retry,
            )

            # 2. Profiling Activity
            self._current_stage = "profile"
            profile_res: ProfileActivityOutput = await workflow.execute_activity(
                profile_activity,
                ProfileActivityInput(data_source_dict=ingest_res.data_source_dict),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=standard_retry,
            )

            # 3. LangGraph Agentic Reasoning (StateGraph execution)
            self._current_stage = "agentic_reasoning"
            graph_res: GraphActivityOutput = await workflow.execute_activity(
                run_forge_graph_activity,
                GraphActivityInput(
                    run_id=input_data.run_id,
                    tenant_id=input_data.tenant_id,
                    source_path=input_data.source_path,
                    output_dir=input_data.output_dir,
                    industry_override=input_data.industry_override,
                    data_answers=input_data.data_answers,
                    binding_confirmations=input_data.binding_confirmations,
                    use_agent=input_data.use_agent,
                    label=input_data.label,
                ),
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=standard_retry,
            )

            self._plugin_dir = graph_res.plugin_dir
            self._status = graph_res.status
            self._error = graph_res.error

            return ForgeWorkflowOutput(
                run_id=input_data.run_id,
                status=self._status,
                plugin_dir=self._plugin_dir,
                error=self._error,
                validation_status=graph_res.validation_status,
            )

        except Exception as exc:
            self._status = "failed"
            self._error = str(exc)
            return ForgeWorkflowOutput(
                run_id=input_data.run_id,
                status="failed",
                error=str(exc),
            )

"""Temporal Worker process listening for Data2plugin generation workflows and activities."""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workers.temporal_worker.activities.activities import (
    ingest_activity,
    package_activity,
    profile_activity,
    run_forge_graph_activity,
    validate_activity,
)
from workers.temporal_worker.workflows.forge_generation import (
    TASK_QUEUE,
    ForgeGenerationWorkflow,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("workers.temporal.worker")


async def run_worker() -> None:
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default")

    logger.info("Connecting to Temporal at %s (namespace: %s)...", temporal_address, temporal_namespace)
    client = await Client.connect(temporal_address, namespace=temporal_namespace)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ForgeGenerationWorkflow],
        activities=[
            ingest_activity,
            profile_activity,
            run_forge_graph_activity,
            validate_activity,
            package_activity,
        ],
    )

    logger.info("Temporal worker started on task queue %r. Awaiting workflows...", TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())

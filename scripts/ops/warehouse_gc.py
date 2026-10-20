#!/usr/bin/env python
"""Reclaim warehouse schemas left behind by abandoned runs.

Every run whose data went into the client warehouse (an upload, or a cloned
source database) owns a `client_<label>_<run_id>` schema + a scoped `_ro`
role. A *successful* run's schema is kept - the shipped plugin queries it
live - and is reclaimed only by `DELETE /runs/{id}`. A run that **failed or
was cancelled** has a plugin that will never install, so its schema is dead
weight; this script drops those once they're older than a cutoff.

    uv run python scripts/ops/warehouse_gc.py --days 7            # dry run
    uv run python scripts/ops/warehouse_gc.py --days 7 --apply

Reads `FORGE_DATABASE_URL` (the jobs DB) and `FORGE_CLIENT_WAREHOUSE_URL`
(the warehouse admin connection) from the environment / `.env`, same as the
API. Intended as a daily cron.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from forge_api.config import get_settings
from forge_core.ingestion.warehouse import deprovision_client_schema
from sqlalchemy import create_engine, text

_DEAD_STATUSES = ("failed", "cancelled")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=7, help="drop schemas whose run last changed more than N days ago"
    )
    parser.add_argument("--apply", action="store_true", help="actually drop; without this it only prints")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.client_warehouse_url:
        print("FORGE_CLIENT_WAREHOUSE_URL is not set - nothing to do.", file=sys.stderr)
        return 1

    # The jobs DB is async-configured for the app; use a plain sync engine here.
    sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")
    engine = create_engine(sync_url)
    cutoff = datetime.now(UTC) - timedelta(days=args.days)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT run_id, warehouse_schema, status, updated_at FROM runs "
                "WHERE warehouse_schema IS NOT NULL AND status IN :dead AND updated_at < :cutoff"
            ),
            {"dead": _DEAD_STATUSES, "cutoff": cutoff},
        ).fetchall()

    if not rows:
        print("No abandoned warehouse schemas older than the cutoff.")
        return 0

    for run_id, schema, status, updated_at in rows:
        line = f"{run_id}  {schema}  ({status}, last change {updated_at})"
        if not args.apply:
            print(f"[dry-run] would drop {line}")
            continue
        try:
            deprovision_client_schema(settings.client_warehouse_url, run_id, schema_name=schema)
            print(f"dropped {line}")
        except Exception as exc:  # noqa: BLE001 - one bad schema shouldn't stop the sweep
            print(f"FAILED {line}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

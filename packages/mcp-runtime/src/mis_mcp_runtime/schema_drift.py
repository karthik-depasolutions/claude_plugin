"""Warn when the live database no longer matches the schema this plugin was
generated against.

The generated pack (schema_model.json, kpi_defs.json, the cookbook) is only
correct for the columns that existed at generation time. `data_source.json`
records those columns per table; this compares them to what the live
connection actually exposes now. A cheap `DESCRIBE` per table, no hashing.
"""

from __future__ import annotations

from typing import Any

_MAX_LISTED = 5


def schema_drift_report(config: Any, con: Any) -> str | None:
    problems: list[str] = []
    for table in config.data_source.tables:
        try:
            live = {row[0] for row in con.execute(f"DESCRIBE {table.physical_ref}").fetchall()}
        except Exception:  # noqa: BLE001 - an unreachable table is itself drift worth reporting
            problems.append(f"{table.name}: not reachable")
            continue
        expected = set(table.columns)
        missing = sorted(expected - live)
        added = sorted(live - expected)
        if not missing and not added:
            continue
        bits = []
        if missing:
            bits.append(f"{len(missing)} column(s) removed ({missing[:_MAX_LISTED]})")
        if added:
            bits.append(f"{len(added)} column(s) added ({added[:_MAX_LISTED]})")
        problems.append(f"{table.name}: " + "; ".join(bits))

    if not problems:
        return None
    return (
        "SCHEMA DRIFT: the live database no longer matches the schema this plugin was generated "
        "for. Its documentation, KPIs, and cookbook queries may be stale — regenerate the plugin. "
        "Differences:\n  - " + "\n  - ".join(problems)
    )

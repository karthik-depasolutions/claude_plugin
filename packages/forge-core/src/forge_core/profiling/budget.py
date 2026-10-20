"""Profiling budget — caps on deterministic work that scales O(n²) or worse.

`ProfilingBudget` is a plain dataclass threaded through the profiling
layer when `agent_schema=True`.  Every cap defaults to a value that is
effectively infinite, so `SMALL_DB_BUDGET` (used when the flag is off)
produces exactly today's behaviour — no output change whatsoever.

`AGENT_SCHEMA_BUDGET` is the production set of caps for 50–500 table
databases.  Every number was chosen so a 50-table database stays under
a 10-minute wall-clock budget on a single core.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfilingBudget:
    """All caps default to effectively-infinite so the flag-off path is
    byte-identical to today's output."""

    # relationships.py: maximum inferred (heuristic) probe queries.
    # Declared FKs never consume from this budget.
    max_inferred_relationship_probes: int = 10_000_000

    # structural.py: how many columns may get a per-column
    # `SELECT DISTINCT <col> LIMIT 5` query for sample values. Beyond this cap,
    # sample values are taken from the rows already fetched at INGEST (zero
    # extra queries) - the single biggest win on a *remote* database, where
    # each of ~hundreds of these round-trips costs real latency + a scan.
    max_distinct_sample_columns: int = 10_000_000

    # valuesets.py: maximum columns for which we fetch a full distinct value
    # set (the enum-decode path).
    max_valueset_columns: int = 10_000_000

    # profiling/__init__.py: skip composite-grain detection (the N^2
    # column-combination scan) for tables with more columns than this.
    skip_grain_composite_above_columns: int = 10_000_000

    # patterns.py: maximum tables to run correlation / temporal analysis
    max_pattern_tables: int = 10_000_000

    # relationships.py: skip overlap probing entirely when either side
    # has more rows than this (the sub-query IN (...) would be huge).
    relationship_probe_row_threshold: int = 100_000_000

    # orchestrator.py: run the deterministic data-quality analysis
    # (per-table reservoir sampling + top-value scans). False in fast mode -
    # another ~one query per table saved on a remote source.
    run_quality_analysis: bool = True


# Flag-off: effectively infinite — zero change to existing output.
SMALL_DB_BUDGET = ProfilingBudget()

# Flag-on: sized so a 50-table *remote* database finishes deterministic
# profiling in a few minutes, not fifteen. The dominant cost on a remote
# source is per-column query round-trips, so those are cut hardest.
AGENT_SCHEMA_BUDGET = ProfilingBudget(
    max_inferred_relationship_probes=300,
    max_distinct_sample_columns=0,  # sample values come from the INGEST rows - no per-column queries
    max_valueset_columns=60,
    skip_grain_composite_above_columns=15,
    max_pattern_tables=12,
    relationship_probe_row_threshold=5_000_000,
)

# Fast mode (`--fast` / no-LLM test run / FORGE_AGENT_SCHEMA_FAST=1): trust the
# declared catalog and do the *minimum* deterministic work - one batched stats
# query per table, declared PK/FK only, nothing O(n²). A 70-table remote
# database profiles in ~2 minutes. Meant for exercising the pipeline end to
# end quickly, not for a production-quality knowledge pack.
FAST_AGENT_SCHEMA_BUDGET = ProfilingBudget(
    max_inferred_relationship_probes=0,      # declared FKs only, zero probes
    max_distinct_sample_columns=0,
    max_valueset_columns=0,                  # no enum-decode scans
    skip_grain_composite_above_columns=0,    # single-column PK detection only
    max_pattern_tables=0,                    # no correlation / FD / redundancy mining
    relationship_probe_row_threshold=1,
    run_quality_analysis=False,              # skip per-table reservoir sampling
)


__all__ = [
    "AGENT_SCHEMA_BUDGET",
    "FAST_AGENT_SCHEMA_BUDGET",
    "ProfilingBudget",
    "SMALL_DB_BUDGET",
]

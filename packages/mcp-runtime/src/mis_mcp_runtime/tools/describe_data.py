"""Tier 1 Discovery Tools: describe_data and list_business_concepts.

Exposes the high-level Business Semantic Model to Claude so it understands the
business domain, entities, dimensions, measures, and available analytical scope
without having to inspect raw database tables.
"""

from __future__ import annotations

from typing import Any

from mis_mcp_runtime.config import RuntimeConfig


def describe_data(config: RuntimeConfig) -> dict[str, Any]:
    """Return high-level business semantic context: domain, core business entities,
    grain of records, dimensions, measures, time fields, and available KPI count.
    """
    ctx = config.business_context or {}
    domain = ctx.get("business_domain") or config.schema_summary.get("pack_slug", "general_business")
    process = ctx.get("business_process") or "analytical_mis"
    record_grain = ctx.get("record_grain") or "one business event/record per row"

    # Extract dimensions, measures, time fields from bindings and schema summary
    entities = ctx.get("entities")
    if not entities:
        entities = [t.name for t in config.data_source.tables if t.name in config.bindings.allowed_tables]

    dimensions = ctx.get("dimensions")
    measures = ctx.get("measures")
    time_fields = ctx.get("time_fields")

    if dimensions is None or measures is None or time_fields is None:
        auto_dims = []
        auto_measures = []
        auto_time = []
        for t in config.schema_summary.get("tables", []):
            if t.get("name") not in config.bindings.allowed_tables:
                continue
            for col in t.get("column_profiles", []):
                name = col.get("column")
                role = col.get("guessed_role", "")
                if role in ("categorical", "geographic", "boolean_flag"):
                    auto_dims.append(name)
                elif role in ("numeric", "currency"):
                    auto_measures.append(name)
                elif role in ("date", "datetime"):
                    auto_time.append(name)

        dimensions = dimensions or list(dict.fromkeys(auto_dims))
        measures = measures or list(dict.fromkeys(auto_measures))
        time_fields = time_fields or list(dict.fromkeys(auto_time))

    return {
        "business_domain": domain,
        "business_process": process,
        "record_grain": record_grain,
        "entities": entities,
        "dimensions": dimensions,
        "measures": measures,
        "time_fields": time_fields,
        "available_kpis": len(config.kpis),
        "available_tables": len(config.bindings.allowed_tables),
        "table_names": config.bindings.allowed_tables,
    }


def list_business_concepts(config: RuntimeConfig) -> dict[str, Any]:
    """Return categorized business taxonomy (entities, dimensions, measures, events)."""
    ctx = config.business_context or {}
    concepts = ctx.get("concepts", {})
    if concepts:
        return concepts

    data_desc = describe_data(config)
    return {
        "entities": data_desc["entities"],
        "dimensions": data_desc["dimensions"],
        "measures": data_desc["measures"],
        "events": ctx.get("events", [f"{e}_recorded" for e in data_desc["entities"]]),
    }

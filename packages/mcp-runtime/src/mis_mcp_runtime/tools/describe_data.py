"""Tier 1 Discovery Tools: describe_data and list_business_concepts.

Exposes the high-level knowledge pack (config/schema_model.json, synthesized
by the generator's LLM pass) to Claude so it understands the business
domain, entities, and analytical scope without inspecting raw tables.
Column-level dimension/measure/time classification comes from the
deterministic structural roles in schema_summary.json.
"""

from __future__ import annotations

from typing import Any

from mis_mcp_runtime.config import RuntimeConfig

_DIMENSION_ROLES = {"categorical", "geographic", "boolean_flag"}
_MEASURE_ROLES = {"numeric", "currency"}
_TIME_ROLES = {"date", "datetime"}


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    for sep in (". ", ".\n"):
        if sep in text:
            return text.split(sep, 1)[0]
    return text


def _classify_columns(config: RuntimeConfig) -> tuple[list[str], list[str], list[str]]:
    dims: list[str] = []
    measures: list[str] = []
    time_fields: list[str] = []
    allowed = set(config.bindings.allowed_tables) | {t.name for t in config.data_source.tables}
    for t in config.schema_summary.get("tables", []):
        if t.get("name") not in allowed:
            continue
        for col in t.get("column_profiles", []):
            role = col.get("guessed_role", "")
            name = col.get("column")
            if role in _DIMENSION_ROLES:
                dims.append(name)
            elif role in _MEASURE_ROLES:
                measures.append(name)
            elif role in _TIME_ROLES:
                time_fields.append(name)
    dedupe = dict.fromkeys
    return list(dedupe(dims)), list(dedupe(measures)), list(dedupe(time_fields))


def describe_data(config: RuntimeConfig) -> dict[str, Any]:
    model = config.schema_model or {}
    model_tables = model.get("tables", [])

    entities = [t["name"] for t in model_tables] or [t.name for t in config.data_source.tables]
    fact = next((t for t in model_tables if t.get("role") == "fact"), None)
    record_grain = (
        (fact or {}).get("grain_prose")
        or (model_tables[0].get("grain_prose") if model_tables else None)
        or "one business record per row"
    )
    dimensions, measures, time_fields = _classify_columns(config)

    return {
        "business_domain": _first_sentence(model.get("overview", ""))
        or config.schema_summary.get("pack_slug", "general_business"),
        "overview": model.get("overview", ""),
        "caveats": model.get("caveats", []),
        "business_process": config.schema_summary.get("pack_slug", "analytical_mis"),
        "record_grain": record_grain,
        "entities": entities,
        "dimensions": dimensions,
        "measures": measures,
        "time_fields": time_fields,
        "relationships": model.get("relationships", []),
        "available_kpis": len(config.kpis),
        "available_tables": len(config.bindings.allowed_tables),
        "table_names": config.bindings.allowed_tables,
    }


def list_business_concepts(config: RuntimeConfig) -> dict[str, Any]:
    """Categorized business taxonomy (entities, dimensions, measures, events)."""
    data_desc = describe_data(config)
    return {
        "entities": data_desc["entities"],
        "dimensions": data_desc["dimensions"],
        "measures": data_desc["measures"],
        "events": [f"{e}_recorded" for e in data_desc["entities"]],
    }

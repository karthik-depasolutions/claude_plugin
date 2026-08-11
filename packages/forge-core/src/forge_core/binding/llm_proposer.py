"""LLM fallback for canonical roles the deterministic scorer couldn't
confidently resolve. Used sparingly — most roles resolve deterministically.
Every proposal is re-validated by the caller (resolver.py) against the real
column list before it is trusted; this module never gets to invent a column.
"""

from __future__ import annotations

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.schema_profile import ColumnProfile

_PROMPT_TEMPLATE = """A customer's table has these columns (name, dtype, guessed structural role,
null percent, cardinality):

{columns}

Which ONE column (if any) best represents this concept: "{role}" — {description}

Return ONLY JSON: {{"column": "exact_column_name_or_null", "confidence": 0.0, "reasoning": "..."}}
The column name MUST be copied exactly from the list above, or null if none of them fit.
"""


def propose_binding(
    role: str,
    description: str,
    table_cols: list[ColumnProfile],
    provider: LLMProvider,
) -> str | None:
    columns_desc = "\n".join(
        f"- {c.name} ({c.dtype}, role={c.guessed_role.value}, "
        f"null%={c.null_percent}, cardinality={c.cardinality})"
        for c in table_cols
    )
    prompt = _PROMPT_TEMPLATE.format(columns=columns_desc, role=role, description=description)
    try:
        result = provider.generate_json(prompt)
    except LLMError:
        return None

    column = result.get("column")
    if not column or column == "null":
        return None
    valid_names = {c.name for c in table_cols}
    return column if column in valid_names else None


__all__ = ["propose_binding"]

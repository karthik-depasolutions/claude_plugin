"""Glossary helpers: business names and deterministic descriptions."""

from __future__ import annotations

import re

from forge_core.models.common import ColumnRole


def business_name_for(col_name: str) -> str:
    # "fee_amount" -> "Fee Amount", "course_id" -> "Course Id"
    return col_name.replace("_", " ").strip().title()


def description_for(
    *,
    col_name: str,
    dtype: str,
    guessed_role: ColumnRole,
    fingerprint: str | None,
    cardinality: int,
    row_count: int,
    top_values: list[tuple[str, int]],
) -> str:
    role_hint = guessed_role.value
    parts: list[str] = []
    name = business_name_for(col_name)
    if fingerprint == "currency":
        parts.append(f"{name} — monetary amount ({dtype}).")
    elif fingerprint == "percent":
        parts.append(f"{name} — percentage/rate ({dtype}, 0-100).")
    elif fingerprint == "phone":
        parts.append(f"{name} — phone number ({dtype}), {cardinality} distinct.")
    elif fingerprint == "aadhaar":
        parts.append(f"{name} — Aadhaar identifier ({dtype}), {cardinality} distinct.")
    elif fingerprint == "pan":
        parts.append(f"{name} — PAN identifier ({dtype}), {cardinality} distinct.")
    elif fingerprint == "url":
        parts.append(f"{name} — URL field ({dtype}).")
    elif fingerprint == "boolean_str":
        vals = ", ".join(f"{v!r}" for v, _ in top_values[:2]) if top_values else "boolean"
        parts.append(f"{name} — boolean as text ({vals}, {dtype}).")
    elif fingerprint == "epoch":
        parts.append(f"{name} — epoch timestamp ({dtype}).")
    elif fingerprint == "enum" and top_values:
        vals = ", ".join(f"{v!r}" for v, _ in top_values[:3])
        parts.append(f"{name} — categorical with {cardinality} distinct values (e.g. {vals}).")
    elif fingerprint in ("iso_date", "iso_datetime"):
        parts.append(f"{name} — timestamp ({dtype}, {fingerprint}).")
    elif guessed_role == ColumnRole.CATEGORICAL and top_values:
        vals = ", ".join(f"{v!r}" for v, _ in top_values[:3])
        parts.append(f"{name} — dimension with {cardinality} distinct values (e.g. {vals}).")
    elif guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME):
        parts.append(f"{name} — timestamp ({dtype}).")
    elif guessed_role == ColumnRole.IDENTIFIER:
        parts.append(f"{name} — identifier ({dtype}), {cardinality} distinct.")
    elif guessed_role == ColumnRole.FREE_TEXT:
        parts.append(f"{name} — free-text field ({dtype}), {cardinality} distinct values.")
    else:
        parts.append(f"{name} — {role_hint} column ({dtype}), {cardinality} distinct.")
    if row_count and cardinality == row_count and guessed_role != ColumnRole.IDENTIFIER:
        parts.append("Values are unique per row.")
    return " ".join(parts)


def unit_for(col_name: str, fingerprint: str | None, dtype: str) -> str | None:
    if fingerprint in ("currency", "percent", "epoch"):
        if fingerprint == "currency":
            if re.search(r"inr|rupee|rs_", col_name.lower()):
                return "INR"
            if re.search(r"usd|\$", col_name.lower()):
                return "USD"
            return "currency"
        if fingerprint == "percent":
            return "%"
        if fingerprint == "epoch":
            return "epoch_seconds"
    if re.search(r"percent|pct|rate", col_name.lower()):
        return "%"
    if re.search(r"days|duration|age", col_name.lower()):
        return "days"
    if fingerprint == "phone":
        return "phone"
    return None

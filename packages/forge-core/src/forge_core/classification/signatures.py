"""Extracts comparable signals from a SchemaProfile — the other half of the
matching equation alongside each pack's hand-curated `PackSignature`."""

from __future__ import annotations

from dataclasses import dataclass

from forge_core.models.schema_profile import SchemaProfile


@dataclass(frozen=True)
class ProfileSignature:
    table_names: frozenset[str]
    column_names: frozenset[str]
    role_categories: frozenset[str]
    table_count: int


def extract_signature(profile: SchemaProfile) -> ProfileSignature:
    table_names = frozenset(t.name.lower() for t in profile.source.tables)
    column_names = frozenset(c.name.lower() for c in profile.structural.columns)
    role_categories = frozenset(c.guessed_role.value for c in profile.structural.columns)
    return ProfileSignature(
        table_names=table_names,
        column_names=column_names,
        role_categories=role_categories,
        table_count=len(profile.source.tables),
    )

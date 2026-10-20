"""Catalog-fact extraction from `information_schema` / `PRAGMA`.

Reads declared foreign keys, primary keys, unique constraints, column
comments, and view membership from the live database without touching
any data rows.  The whole point of this module is to let relationship
detection skip its O(n²) value-overlap probes for pairs where a
database-declared FK already answers the question with 100 % confidence.

Supported sources
-----------------
* Postgres  — four information_schema queries via DuckDB's postgres extension
* SQLite    — PRAGMA foreign_key_list / PRAGMA table_info
* Everything else (file sources) — returns empty CatalogFacts; the
  downstream callers handle None / empty cleanly.

The public surface is intentionally small:

    catalog = read_catalog(con, source_kind=SourceKind.POSTGRES,
                           catalog_alias="srcdb", schema="public")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import duckdb

from forge_core.models.common import SourceKind

# A schema / catalog-alias name we are willing to interpolate into SQL. Postgres
# identifiers can be almost anything when quoted, but the names we ever pass here
# come from a connection string's search_path or a fixed literal ("srcdb"), so a
# conservative allowlist closes the injection surface with no real-world loss.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class ForeignKeyFact:
    """One declared FK constraint (possibly composite — rare but real)."""

    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    constraint_name: str = ""


@dataclass(frozen=True)
class CatalogFacts:
    """All constraint-level metadata read from the live database.

    Every field defaults to an empty collection so callers can always
    destructure without checking for None.
    """

    # table -> [pk_column, ...]  (multi-column PK keeps order)
    primary_keys: dict[str, list[str]] = field(default_factory=dict)
    foreign_keys: list[ForeignKeyFact] = field(default_factory=list)
    # table -> [[col, ...], ...]  (one inner list per unique constraint)
    unique_keys: dict[str, list[list[str]]] = field(default_factory=dict)
    # table -> {column, ...}  declared NOT NULL (beyond PK, which is always NN)
    not_null: dict[str, set[str]] = field(default_factory=dict)
    # "table" or "table.column" -> comment text
    comments: dict[str, str] = field(default_factory=dict)
    # set of table names that are actually views
    views: set[str] = field(default_factory=set)

    def declared_fk_pairs(self) -> set[tuple[str, str, str, str]]:
        """Return all (from_table, from_col, to_table, to_col) tuples.

        For composite FKs every column combination is included, which is
        correct for the name-match short-circuit used in relationships.py.
        """
        pairs: set[tuple[str, str, str, str]] = set()
        for fk in self.foreign_keys:
            for fc, tc in zip(fk.from_columns, fk.to_columns, strict=False):
                pairs.add((fk.from_table, fc, fk.to_table, tc))
        return pairs

    @property
    def empty(self) -> bool:
        return (
            not self.primary_keys
            and not self.foreign_keys
            and not self.unique_keys
            and not self.comments
            and not self.views
        )


_EMPTY = CatalogFacts()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def read_catalog(
    con: duckdb.DuckDBPyConnection,
    *,
    source_kind: SourceKind,
    catalog_alias: str = "srcdb",
    schema: str = "public",
) -> CatalogFacts:
    """Return catalog facts for the attached source database.

    Returns an empty `CatalogFacts` for any source kind that has no
    declarative constraint system (file adapters).  Never raises — a
    catalog read is best-effort: one broken information_schema query must
    never abort a profiling run.
    """
    if not _SAFE_IDENT.match(catalog_alias) or not _SAFE_IDENT.match(schema):
        return _EMPTY
    try:
        if source_kind == SourceKind.POSTGRES:
            return _read_postgres(con, catalog_alias=catalog_alias, schema=schema)
        if source_kind == SourceKind.SQLITE:
            return _read_sqlite(con, catalog_alias=catalog_alias)
    except Exception:  # noqa: BLE001
        pass
    return _EMPTY


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------

def _read_postgres(
    con: duckdb.DuckDBPyConnection,
    *,
    catalog_alias: str,
    schema: str,
) -> CatalogFacts:
    """Four passthrough queries against information_schema via DuckDB's
    postgres extension.  DuckDB routes `postgres_query(alias, sql)` straight
    to the attached Postgres server — no local temp tables created."""

    def pg(sql: str) -> list[tuple]:
        return con.execute(f"SELECT * FROM postgres_query('{catalog_alias}', $${sql}$$)").fetchall()

    # --- Primary and unique keys ------------------------------------------
    kc_rows = pg(f"""
        SELECT tc.constraint_type, tc.constraint_name, tc.table_name,
               kc.column_name, kc.ordinal_position
        FROM   information_schema.table_constraints tc
        JOIN   information_schema.key_column_usage kc
               ON  kc.constraint_name  = tc.constraint_name
               AND kc.table_schema     = tc.table_schema
               AND kc.table_name       = tc.table_name
        WHERE  tc.table_schema = '{schema}'
        AND    tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        ORDER  BY tc.constraint_name, kc.ordinal_position
    """)

    primary_keys: dict[str, list[str]] = {}
    unique_keys: dict[str, list[list[str]]] = {}
    # group by (constraint_type, constraint_name, table_name)
    _kc_groups: dict[tuple[str, str, str], list[str]] = {}
    for ctype, cname, tname, colname, _ord in kc_rows:
        key = (ctype, cname, tname)
        _kc_groups.setdefault(key, []).append(colname)
    for (ctype, _cname, tname), cols in _kc_groups.items():
        if ctype == "PRIMARY KEY":
            primary_keys[tname] = cols
        else:
            unique_keys.setdefault(tname, []).append(cols)

    # --- Foreign keys --------------------------------------------------------
    fk_rows = pg(f"""
        SELECT tc.constraint_name, tc.table_name       AS from_table,
               kcu.column_name                         AS from_col,
               ccu.table_name                          AS to_table,
               ccu.column_name                         AS to_col,
               kcu.ordinal_position
        FROM   information_schema.table_constraints tc
        JOIN   information_schema.key_column_usage kcu
               ON  kcu.constraint_name = tc.constraint_name
               AND kcu.table_schema    = tc.table_schema
        JOIN   information_schema.constraint_column_usage ccu
               ON  ccu.constraint_name = tc.constraint_name
               AND ccu.table_schema    = tc.table_schema
        WHERE  tc.table_schema     = '{schema}'
        AND    tc.constraint_type  = 'FOREIGN KEY'
        ORDER  BY tc.constraint_name, kcu.ordinal_position
    """)

    fk_groups: dict[tuple[str, str, str], tuple[list[str], list[str]]] = {}
    for cname, from_tbl, from_col, to_tbl, to_col, _ord in fk_rows:
        key = (cname, from_tbl, to_tbl)
        if key not in fk_groups:
            fk_groups[key] = ([], [])
        fk_groups[key][0].append(from_col)
        fk_groups[key][1].append(to_col)

    foreign_keys: list[ForeignKeyFact] = [
        ForeignKeyFact(
            constraint_name=cname,
            from_table=from_tbl,
            from_columns=from_cols,
            to_table=to_tbl,
            to_columns=to_cols,
        )
        for (cname, from_tbl, to_tbl), (from_cols, to_cols) in fk_groups.items()
    ]

    # --- NOT NULL ------------------------------------------------------------
    nn_rows = pg(f"""
        SELECT table_name, column_name
        FROM   information_schema.columns
        WHERE  table_schema = '{schema}'
        AND    is_nullable  = 'NO'
    """)
    not_null: dict[str, set[str]] = {}
    for tname, colname in nn_rows:
        not_null.setdefault(tname, set()).add(colname)

    # --- Comments (tables and columns) ---------------------------------------
    comments: dict[str, str] = {}
    try:
        tc_rows = pg(f"""
            SELECT obj_description(c.oid, 'pg_class') AS tbl_comment,
                   c.relname                           AS tbl_name
            FROM   pg_catalog.pg_class   c
            JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE  n.nspname = '{schema}'
            AND    c.relkind IN ('r', 'v', 'm')
            AND    obj_description(c.oid, 'pg_class') IS NOT NULL
        """)
        for comment, tname in tc_rows:
            if comment:
                comments[tname] = comment.strip()

        cc_rows = pg(f"""
            SELECT c.relname  AS tbl,
                   a.attname  AS col,
                   col_description(a.attrelid, a.attnum) AS cmt
            FROM   pg_catalog.pg_attribute a
            JOIN   pg_catalog.pg_class     c ON c.oid = a.attrelid
            JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE  n.nspname      = '{schema}'
            AND    a.attnum       > 0
            AND    NOT a.attisdropped
            AND    col_description(a.attrelid, a.attnum) IS NOT NULL
        """)
        for tname, colname, cmt in cc_rows:
            if cmt:
                comments[f"{tname}.{colname}"] = cmt.strip()
    except Exception:  # noqa: BLE001  — comment queries are optional
        pass

    # --- Views ---------------------------------------------------------------
    view_rows = pg(f"""
        SELECT table_name FROM information_schema.views
        WHERE  table_schema = '{schema}'
    """)
    views: set[str] = {row[0] for row in view_rows}

    return CatalogFacts(
        primary_keys=primary_keys,
        foreign_keys=foreign_keys,
        unique_keys=unique_keys,
        not_null=not_null,
        comments=comments,
        views=views,
    )


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

def _read_sqlite(
    con: duckdb.DuckDBPyConnection,
    *,
    catalog_alias: str,
) -> CatalogFacts:
    """Read FK / PK facts from SQLite using PRAGMA passthrough."""

    def _tables() -> list[str]:
        rows = con.execute(
            f"SELECT table_name FROM duckdb_tables() "
            f"WHERE database_name = '{catalog_alias}' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    def _pragma(tbl: str, pragma: str) -> list[tuple]:
        # DuckDB lets us run PRAGMA via sqlite_query on an attached SQLite DB.
        return con.execute(
            f"SELECT * FROM sqlite_query('{catalog_alias}', 'PRAGMA {pragma}(\"{tbl}\")')"
        ).fetchall()

    table_names = _tables()
    primary_keys: dict[str, list[str]] = {}
    foreign_keys: list[ForeignKeyFact] = []
    not_null: dict[str, set[str]] = {}
    comments: dict[str, str] = {}
    views: set[str] = set()

    def _int(v: Any, default: int = 0) -> int:
        # sqlite_query() returns every PRAGMA column as text - coerce the
        # numeric flags (notnull / pk / fk-id) back to int before comparing.
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    for tname in table_names:
        # table_info: (cid, name, type, notnull, dflt_value, pk)
        ti_rows = _pragma(tname, "table_info")
        pk_cols = [(_int(r[5]), r[1]) for r in ti_rows if _int(r[5]) > 0]  # (pk ordinal, col)
        if pk_cols:
            pk_cols.sort()
            primary_keys[tname] = [c for _, c in pk_cols]
        nn_cols = {r[1] for r in ti_rows if _int(r[3]) > 0}  # notnull flag
        if nn_cols:
            not_null[tname] = nn_cols

        # foreign_key_list: (id, seq, table, from, to, ...)
        fk_rows = _pragma(tname, "foreign_key_list")
        # group by fk id, preserving column order via seq
        fk_by_id: dict[int, tuple[str, list[str], list[str]]] = {}
        for row in sorted(fk_rows, key=lambda r: (_int(r[0]), _int(r[1]))):
            fk_id, _seq, to_tbl, from_col, to_col = _int(row[0]), row[1], row[2], row[3], row[4]
            if fk_id not in fk_by_id:
                fk_by_id[fk_id] = (to_tbl, [], [])
            fk_by_id[fk_id][1].append(from_col)
            fk_by_id[fk_id][2].append(to_col)
        for fk_id, (to_tbl, from_cols, to_cols) in fk_by_id.items():
            foreign_keys.append(
                ForeignKeyFact(
                    from_table=tname,
                    from_columns=from_cols,
                    to_table=to_tbl,
                    to_columns=to_cols,
                    constraint_name=f"fk_{tname}_{fk_id}",
                )
            )

    # SQLite views via DuckDB internal catalog
    try:
        view_rows = con.execute(
            f"SELECT table_name FROM duckdb_views() WHERE database_name = '{catalog_alias}'"
        ).fetchall()
        views = {r[0] for r in view_rows}
    except Exception:  # noqa: BLE001
        pass

    return CatalogFacts(
        primary_keys=primary_keys,
        foreign_keys=foreign_keys,
        unique_keys={},
        not_null=not_null,
        comments=comments,
        views=views,
    )


__all__ = ["CatalogFacts", "ForeignKeyFact", "read_catalog"]

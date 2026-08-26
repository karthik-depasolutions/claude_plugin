"""Layer 1 — deterministic structural profiling. No LLM call in this module,
ever (see docs/architecture.md §4.2). Works uniformly across every table in
a possibly multi-table DataSource.
"""

from __future__ import annotations

import os
import re

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile, TableGrain

# Value-shape tests, matched against real sample values or via a real
# TRY_CAST query - never a column name. A name is evidence to show an
# agent, never a decision rule (see docs/adr on structural-vs-semantic
# triage). Deliberately absent from here: currency, geographic, email,
# phone, "is this a business identifier" by name - none of those are
# determinable from shape alone (email/phone have no distinctive-enough
# value shape to test cheaply here without colliding with other formats -
# `profiling/data_map.py`'s format_fingerprint owns that, with real
# anchored regexes); identifier is a genuine structural fact computed below
# (uniqueness + a surrogate-key-shaped type), everything else is an agent
# claim, gate-verified before anything treats it as real.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

_PERSON_NAME_HINTS = re.compile(
    r"(customer|patient|guardian|contact|user|full|first|last|student|employee|client|"
    r"account_holder|owner)[_\s]*name|^name$",
    re.IGNORECASE,
)
_OTHER_PII_HINTS = re.compile(
    r"phone|email|address|dob|birth|ssn|aadhaar|pan_number|passport|account_number|card_number",
    re.IGNORECASE,
)

_NUMERIC_DUCKDB_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL",
}
_INTEGER_DUCKDB_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
}
_TEMPORAL_DUCKDB_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIME"}
_BOOLEAN_DUCKDB_TYPES = {"BOOLEAN"}


def _base_type(dtype: str) -> str:
    return dtype.split("(", maxsplit=1)[0].strip().upper()


# Text date formats DuckDB's plain CAST cannot read. Ordered most- to
# least- common in real business exports. Day-first precedes month-first
# because the datasets this system is built for are predominantly Indian and
# European; where the values themselves disambiguate, that evidence wins over
# this ordering (see `_detect_temporal_format`).
_TEXT_DATE_FORMATS = (
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%m-%d-%Y", "%m/%d/%Y",
    "%Y/%m/%d", "%Y%m%d",
    "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
    "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
)
_DAY_FIRST = {"%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"}
_MONTH_FIRST = {"%m-%d-%Y", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"}


def _detect_temporal_format(
    con: duckdb.DuckDBPyConnection, ref: str, quoted: str, row_count: int
) -> tuple[bool, str | None]:
    """Is this text column a date, and if so in what format?

    Returns `(is_temporal, strptime_format)`. A `None` format means plain
    `CAST` already works (ISO-8601); a string means every value parses under
    that `strptime` pattern and *only* that will read it.

    Why this exists: `CAST('02-05-1993' AS TIMESTAMP)` does not return NULL
    in DuckDB, it raises - so a KPI whose SQL casts a DD-MM-YYYY column
    fails the whole build at dry-run rather than degrading. Detecting the
    real format lets the binding carry a `strptime(...)` expression instead,
    which is what makes non-ISO exports (very common in Indian and European
    data) usable at all rather than a hard failure.

    Values, never names, decide - same rule as every other role here."""
    if row_count == 0:
        return False, None

    def _fully_parses(expression: str) -> bool:
        row = con.execute(
            f"SELECT COUNT(*) FILTER (WHERE {quoted} IS NOT NULL) AS total, "
            f"COUNT(*) FILTER (WHERE {quoted} IS NOT NULL AND {expression} IS NOT NULL) AS parsed "
            f"FROM {ref}"
        ).fetchone()
        assert row is not None
        total, parsed = row
        return total > 0 and total == parsed

    # ISO first: no expression needed downstream, so it stays the cheap path.
    if _fully_parses(f"TRY_CAST({quoted} AS DATE)"):
        return True, None

    matching = [
        fmt
        for fmt in _TEXT_DATE_FORMATS
        if _fully_parses(f"TRY_STRPTIME({quoted}, '{fmt}')")
    ]
    if not matching:
        return False, None

    # "02-05-1993" is a real date under both DD-MM and MM-DD. Let the data
    # break the tie: a first field above 12 can only be a day, a second field
    # above 12 can only be a month. Only when no row disambiguates do we fall
    # back to the ordering above.
    day_first = [f for f in matching if f in _DAY_FIRST]
    month_first = [f for f in matching if f in _MONTH_FIRST]
    if day_first and month_first:
        first_field_over_12 = _fully_parses(
            f"CASE WHEN TRY_CAST(SPLIT_PART({quoted}, "
            f"CASE WHEN {quoted} LIKE '%/%' THEN '/' WHEN {quoted} LIKE '%.%' THEN '.' ELSE '-' END, 1) "
            f"AS INTEGER) > 12 THEN 1 END"
        )
        if first_field_over_12:
            return True, day_first[0]

    return True, matching[0]


def _looks_temporal(con: duckdb.DuckDBPyConnection, ref: str, quoted: str, row_count: int) -> bool:
    """Many sources (SQLite chief among them) store dates as plain TEXT -
    dtype alone can't see them. A query against the real values is the same
    pattern `validation/plausibility.py`'s mixed_types check already uses for
    numeric parseability: not a `_at$`/`_on$` name guess, and it can't be
    fooled by a phone number or a name that happens to contain a hyphen the
    way a loose value-regex can."""
    is_temporal, _fmt = _detect_temporal_format(con, ref, quoted, row_count)
    return is_temporal


def _guess_role(
    con: duckdb.DuckDBPyConnection, ref: str, quoted: str, dtype: str, cardinality: int, row_count: int
) -> ColumnRole:
    """Shape and real-value evidence only - no column NAME is ever
    consulted. `CURRENCY`/`GEOGRAPHIC`/name-derived `IDENTIFIER` are gone:
    those are semantic claims, not shapes, and are resolved either by a
    genuine structural fact (uniqueness of a surrogate-key-shaped column,
    see `_is_structural_identifier` below) or by a gate-verified agent
    claim (`ColumnClaim`) - never by a substring match on an English
    column name, which silently fails on non-English data and is what let
    a column named `total_score` become `CURRENCY` (and therefore
    summable) purely by coincidence."""
    base = _base_type(dtype)

    if base in _BOOLEAN_DUCKDB_TYPES:
        return ColumnRole.BOOLEAN_FLAG
    if base in _TEMPORAL_DUCKDB_TYPES:
        return ColumnRole.DATETIME if "TIMESTAMP" in base or "TIME" in base else ColumnRole.DATE

    if base in _NUMERIC_DUCKDB_TYPES:
        if row_count and cardinality <= 2:
            return ColumnRole.BOOLEAN_FLAG
        return ColumnRole.NUMERIC

    if _looks_temporal(con, ref, quoted, row_count):
        return ColumnRole.DATE

    if row_count and cardinality <= max(1, min(30, row_count // 2)):
        return ColumnRole.CATEGORICAL

    return ColumnRole.FREE_TEXT


def _is_structural_identifier(cardinality: int, row_count: int, null_count: int) -> bool:
    """A complete primary-key test on its own (the same test `grain.py`
    already uses for PK detection) - real uniqueness, not a `*_id`/`*_key`
    name suffix, and not restricted to a particular dtype (a business ID
    like "B1002" is a perfectly legitimate string key). The remaining
    problem - a small dimension table's genuine label column being just as
    unique as its real key - is a table-wide tie, not a per-column shape
    question, so it's resolved by `_demote_tied_identifiers` below using
    every column of the table at once, not here."""
    return row_count > 0 and null_count == 0 and cardinality == row_count


def _demote_tied_identifiers(columns: list[ColumnProfile]) -> list[ColumnProfile]:
    """Table-wide second pass, mirroring `reclassify_dimension_labels`'s
    own pattern (a per-column shape test can't see sibling columns, so
    disambiguation that needs table-wide context happens as a follow-up
    pass, not by cramming more state into `_guess_role`). When more than
    one column in the same table is independently unique - a small
    dimension table's label column is exactly as unique as its real key,
    by construction - only one may keep the IDENTIFIER role: prefer an
    integer or UUID-shaped column (the conventional surrogate-key shape),
    else the first candidate in physical column order. The rest fall back
    to whatever `_guess_role` would have assigned had it not tied on
    uniqueness - never all of them, or a genuine dimension label like
    `course_name` gets misclassified as a key instead of a group-by column."""
    by_table: dict[str, list[ColumnProfile]] = {}
    for col in columns:
        by_table.setdefault(col.table, []).append(col)

    demote: set[tuple[str, str]] = set()
    for table_cols in by_table.values():
        # Only columns actually PROMOTED to IDENTIFIER compete here - a
        # DATE or BOOLEAN column can be just as unique per row (one date
        # per order is normal) without that meaning anything about which
        # column is the table's key; is_likely_identifier alone would
        # wrongly sweep those up too and clobber an already-correct role.
        candidates = [c for c in table_cols if c.guessed_role == ColumnRole.IDENTIFIER]
        if len(candidates) <= 1:
            continue
        shaped = [
            c
            for c in candidates
            if _base_type(c.dtype) in _INTEGER_DUCKDB_TYPES
            or (c.sample_values and all(_UUID_RE.match(v) for v in c.sample_values))
        ]
        winner = (shaped or candidates)[0]
        demote.update((c.table, c.name) for c in candidates if c is not winner)

    if not demote:
        return columns
    # Demote to the role the column's own dtype implies, which is what
    # `_guess_role` would have returned had uniqueness not promoted it.
    # For TEXT that is FREE_TEXT (the cardinality rule leaves a unique-per-row
    # text column there once row_count > 1), and reclassify_dimension_labels
    # (P2-02) still runs after this to promote a genuine label back to
    # CATEGORICAL using grain. But a NUMERIC column reaches NUMERIC on dtype
    # alone, before any cardinality rule - blanket-demoting it to FREE_TEXT
    # silently destroyed every measure on small tables, where a real metric
    # column (a score, an amount) is unique purely by coincidence of row
    # count and could then never be selected as a measure at all.
    def _demoted_role(col: ColumnProfile) -> ColumnRole:
        base = _base_type(col.dtype)
        if base in _NUMERIC_DUCKDB_TYPES:
            return ColumnRole.NUMERIC
        if base in _TEMPORAL_DUCKDB_TYPES:
            return ColumnRole.DATETIME if "TIMESTAMP" in base or "TIME" in base else ColumnRole.DATE
        return ColumnRole.FREE_TEXT

    return [
        col.model_copy(update={"guessed_role": _demoted_role(col), "is_likely_identifier": False})
        if (col.table, col.name) in demote
        else col
        for col in columns
    ]


def _is_likely_pii(name: str, role: ColumnRole, sample_values: list[str]) -> bool:
    """Value-shape PII (email/phone) is a real structural fact - keep it.
    Everything else (a person's name, an address) has no value-shape
    signature at all; today's name-substring fallback below is a known,
    documented gap (specifically because this can't yet be trusted as a
    final decision on non-English data - closing it properly is an
    agent-claim + gate problem, not a bigger regex).

    Single gate, per docs/adr: FORGE_ENABLE_PII_PROTECTION defaults to
    false during testing, so this always returns False and no column is
    ever denied/redacted - detection logic stays intact underneath (every
    branch below still runs and is exercised by tests), so turning
    protection back on before any real customer's data reaches this
    system is one env var, not a rewrite. This is the single source
    `ColumnProfile.is_likely_pii` is computed from, so gating here alone
    covers every downstream consumer (denial, redaction, dimension/measure
    candidacy, the data map's top-values suppression, ...) without
    touching each of them individually."""
    if os.environ.get("FORGE_ENABLE_PII_PROTECTION", "false").strip().lower() not in ("1", "true", "yes"):
        return False
    if role in (ColumnRole.EMAIL, ColumnRole.PHONE):
        return True
    lower = name.lower()
    return bool(_PERSON_NAME_HINTS.search(lower) or _OTHER_PII_HINTS.search(lower))


def _profile_column(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, col_name: str, dtype: str, row_count: int
) -> ColumnProfile:
    ref = table.physical_ref
    quoted = f'"{col_name}"'
    stats = con.execute(
        f"SELECT COUNT(*) FILTER (WHERE {quoted} IS NULL), COUNT(DISTINCT {quoted}) FROM {ref}"
    ).fetchone()
    assert stats is not None  # an aggregate query always returns exactly one row
    null_count, cardinality = int(stats[0]), int(stats[1])
    null_percent = round((null_count / row_count * 100.0) if row_count else 0.0, 2)
    distinct_ratio = round((cardinality / row_count) if row_count else 0.0, 4)

    min_value = max_value = None
    sample_values: list[str] = []
    base = _base_type(dtype)
    if base in _NUMERIC_DUCKDB_TYPES:
        mm = con.execute(f"SELECT MIN({quoted}), MAX({quoted}) FROM {ref}").fetchone()
        assert mm is not None
        min_value, max_value = mm[0], mm[1]
    else:
        rows = con.execute(
            f"SELECT DISTINCT {quoted} FROM {ref} WHERE {quoted} IS NOT NULL LIMIT 5"
        ).fetchall()
        sample_values = [str(r[0]) for r in rows]

    role = _guess_role(con, ref, quoted, dtype, cardinality, row_count)
    is_identifier = _is_structural_identifier(cardinality, row_count, null_count)
    if is_identifier and role in (ColumnRole.NUMERIC, ColumnRole.CATEGORICAL, ColumnRole.FREE_TEXT):
        role = ColumnRole.IDENTIFIER

    # Only text columns can need a strptime pattern; a native DATE/TIMESTAMP
    # already casts. Recomputed rather than threaded out of _guess_role so
    # that function keeps its single "what role is this" responsibility.
    temporal_format: str | None = None
    if role in (ColumnRole.DATE, ColumnRole.DATETIME) and _base_type(dtype) not in _TEMPORAL_DUCKDB_TYPES:
        _is_temporal, temporal_format = _detect_temporal_format(con, ref, quoted, row_count)

    return ColumnProfile(
        table=table.name,
        name=col_name,
        dtype=dtype,
        null_percent=null_percent,
        cardinality=cardinality,
        distinct_ratio=min(distinct_ratio, 1.0),
        guessed_role=role,
        min_value=min_value,
        max_value=max_value,
        sample_values=sample_values,
        is_likely_identifier=is_identifier,
        is_likely_pii=_is_likely_pii(col_name, role, sample_values),
        temporal_format=temporal_format,
    )


def reclassify_dimension_labels(
    data_source: DataSource, columns: list[ColumnProfile], grains: list[TableGrain]
) -> list[ColumnProfile]:
    """P2-02, grain-aware second pass over FREE_TEXT columns only - runs
    after grain inference, since it needs grains that depend on the columns
    it's revising. `_guess_role`'s `row_count // 2` rule is exactly inverted
    for a dimension table: every genuine dimension label has
    cardinality == row_count BY DESIGN (one row per distinct thing), which
    is precisely what made `courses.course_name` (4/4 rows) fail the old
    test and get destroyed (review P1.2). This is deliberately just a
    reclassification of FREE_TEXT -> CATEGORICAL, never the reverse -
    nothing here can make a column MORE likely to be denied."""
    grain_by_table = {g.table: g for g in grains}
    row_count_by_table = {t.name: t.row_count for t in data_source.tables}
    updated: list[ColumnProfile] = []
    for col in columns:
        # Never promote a PII-shaped column - low cardinality (a small
        # customer roster) is not what makes a personal-name column a
        # legitimate dimension to group by. Denial already keys off
        # is_likely_pii independently of guessed_role, so this guard is
        # about keeping the role label itself honest, not a safety fix.
        if col.guessed_role != ColumnRole.FREE_TEXT or col.is_likely_pii:
            updated.append(col)
            continue

        row_count = row_count_by_table.get(col.table, 0)
        grain = grain_by_table.get(col.table)
        is_dimension_shaped = grain is not None and grain.confidence >= 0.8 and len(grain.grain_columns) == 1
        avg_length = (
            sum(len(v) for v in col.sample_values) / len(col.sample_values) if col.sample_values else 0.0
        )

        promote = (is_dimension_shaped and col.cardinality == row_count and avg_length < 60) or (
            row_count > 0 and col.cardinality <= max(2, min(50, int(row_count**0.5) * 3))
        )
        updated.append(col.model_copy(update={"guessed_role": ColumnRole.CATEGORICAL}) if promote else col)
    return updated


def build_structural_profile(data_source: DataSource, con: duckdb.DuckDBPyConnection) -> StructuralProfile:
    columns: list[ColumnProfile] = []
    for table in data_source.tables:
        for col in table.columns:
            columns.append(_profile_column(con, table, col.name, col.raw_dtype, table.row_count))
    return StructuralProfile(columns=_demote_tied_identifiers(columns))

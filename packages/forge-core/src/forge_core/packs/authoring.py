"""`forge packs {new,lint,test}` internals.

Composes the exact functions the pipeline uses - `load_pack`, `ingest`,
`build_structural_only`, `resolve_bindings`, `compile_all`, `open_session`,
`score_pack` - so what a pack author sees here is what a real run will do.
No LLM anywhere in this module.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from forge_core.binding.resolver import resolve_bindings
from forge_core.classification.loader import load_pack
from forge_core.classification.matcher import score_pack
from forge_core.classification.signatures import extract_signature
from forge_core.compiler.kpi_compiler import compile_all
from forge_core.compiler.sql_render import required_tokens
from forge_core.ingestion.registry import ingest
from forge_core.models.industry_pack import CanonicalKpi
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only
from forge_core.runtime_session import open_session

# --------------------------------------------------------------------------- #
# lint
# --------------------------------------------------------------------------- #


@dataclass
class LintFinding:
    level: str  # "error" | "warn"
    where: str  # "pack" | "kpi:<id>" | "template:<id>"
    message: str


def _kpi_required_roles(kpi: CanonicalKpi) -> list[str]:
    r = kpi.requires
    return [*r.measures, *r.dimensions, *r.filters, *r.entities]


_AS_ALIAS_RE = re.compile(r"\bAS\s+([A-Za-z_]\w*)", re.IGNORECASE)


def _sql_output_names(template: str) -> set[str]:
    """Result column names, read straight off the `AS <name>` clauses in the
    KPI template. A rendered/parsed approach is impossible before binding, and
    every pack KPI names its outputs explicitly."""
    return set(_AS_ALIAS_RE.findall(template))


def _looks_like_select(template: str) -> bool:
    head = template.lstrip().upper()
    return (head.startswith("SELECT") or head.startswith("WITH")) and " FROM " in f" {template.upper()} "


def _assertion_names(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


_ASSERTION_BUILTINS = {"abs", "min", "max", "len", "sum", "round", "all", "any", "True", "False", "None"}


def lint_pack(pack_dir: Path) -> list[LintFinding]:
    """Semantic checks beyond the pydantic model: role references resolve,
    every KPI `{{token}}` is a real role / alias / value-set, templates exist,
    assertions reference real result columns."""
    findings: list[LintFinding] = []
    pack = load_pack(pack_dir)  # raises on pydantic / json errors - caller handles

    roles = set(pack.canonical_roles)
    aliases = set(pack.table_aliases) | {"fact"}
    value_sets = set(pack.value_set_hints)
    known_tokens = roles | aliases | value_sets

    for key in pack.role_hints:
        if key not in roles:
            findings.append(LintFinding("error", "pack", f"role_hints key {key!r} is not a canonical role"))
    for key in pack.value_set_hints:
        if key not in {t for kpi in pack.kpis for t in required_tokens(kpi.sql)}:
            findings.append(
                LintFinding("warn", "pack", f"value_set_hints key {key!r} is used by no KPI template")
            )

    lo, hi = pack.signature.table_count_range
    if lo > hi:
        findings.append(LintFinding("error", "pack", f"signature.table_count_range {lo} > {hi}"))

    for name, entity in pack.entities.items():
        for r in entity.roles:
            if r not in roles:
                findings.append(
                    LintFinding("error", "pack", f"entity {name!r} lists role {r!r} not in canonical_roles")
                )

    for kpi in pack.kpis:
        where = f"kpi:{kpi.id}"
        for role in _kpi_required_roles(kpi):
            if role not in roles:
                findings.append(LintFinding("error", where, f"requires role {role!r} not in canonical_roles"))
        for js in kpi.joins:
            if js.entity not in pack.entities:
                findings.append(LintFinding("error", where, f"joins entity {js.entity!r} not in entities"))
            if js.via not in roles:
                findings.append(LintFinding("error", where, f"joins via {js.via!r} is not a canonical role"))

        for token in sorted(required_tokens(kpi.sql)):
            if token not in known_tokens:
                findings.append(
                    LintFinding("error", where, f"token {{{{{token}}}}} is not a role/alias/value-set")
                )

        # The `sql` is a template - it cannot be fully parsed before binding
        # (that is what `forge packs test` does). A cheap shape check catches
        # obvious typos; real SQL validation is the compiler + dry_run.
        if not _looks_like_select(kpi.sql):
            findings.append(LintFinding("error", where, "sql template is not a SELECT/WITH with a FROM"))

        outputs = _sql_output_names(kpi.sql)
        for assertion in kpi.assertions:
            unknown = _assertion_names(assertion) - outputs - _ASSERTION_BUILTINS
            if unknown:
                findings.append(
                    LintFinding("warn", where, f"assertion {assertion!r} names non-result {sorted(unknown)}")
                )

    # template_path files are not checked: the generator currently writes
    # skills/agents/commands from LLM prose and does not read pack template
    # files (the field is forward-looking metadata). requires_roles is still
    # meaningful.
    for refs in (pack.skill_templates, pack.agent_templates, pack.recipe_templates):
        for ref in refs:
            for role in ref.requires_roles:
                if role not in roles:
                    findings.append(
                        LintFinding(
                            "error", f"template:{ref.id}", f"requires_roles {role!r} is not a canonical role"
                        )
                    )

    return findings


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #

_PACK_TEMPLATE = {
    "slug": "",
    "name": "",
    "version": "0.1.0",
    "description": "One-line description. Keep it low-confidence unless the signature is strong.",
    "vocabulary": [],
    "canonical_roles": {
        "revenue_amount": "the money value of one transaction",
        "transaction_date": "when the transaction happened",
    },
    "role_hints": {"revenue_amount": ["amount", "total", "revenue", "price"]},
    "table_aliases": {"fact": "the primary transaction/event table"},
    "value_set_hints": {},
    "skill_templates": [],
    "agent_templates": [],
    "recipe_templates": [],
    "guardrails": {
        "denied_role_categories": ["free_text"],
        "require_read_only": True,
        "max_query_rows": 200,
        "query_timeout_seconds": 10,
        "notes": [],
    },
    "signature": {
        "entity_name_hints": [],
        "column_name_hints": [],
        "required_role_categories": [],
        "table_count_range": [1, 20],
    },
}

_EXAMPLE_KPI = {
    "id": "total_revenue",
    "label": "Total revenue",
    "description": "Sum of the revenue amount across all transactions.",
    "formula_plain_english": "sum(revenue_amount)",
    "grain": "transaction",
    "requires": {"measures": ["revenue_amount"], "dimensions": [], "filters": [], "entities": []},
    "sql": "SELECT SUM({{revenue_amount}}) AS total_revenue FROM {{fact}}",
    "unit": "currency",
    "assertions": ["total_revenue >= 0"],
    "optional": False,
}


def scaffold_pack(slug: str, packs_root: Path) -> Path:
    """Create `<packs_root>/<slug>/` with a starter pack.json, one example
    KPI, and empty skills/agents/recipes dirs. Returns the pack directory."""
    if not slug.replace("-", "").isalnum() or not slug[0].isalpha():
        raise ValueError(f"slug {slug!r} must be kebab-case starting with a letter")
    pack_dir = packs_root / slug
    if pack_dir.exists():
        raise FileExistsError(f"{pack_dir} already exists")

    (pack_dir / "kpis").mkdir(parents=True)
    for d in ("skills", "agents", "recipes"):
        (pack_dir / d).mkdir()
        (pack_dir / d / ".gitkeep").touch()

    manifest = {**_PACK_TEMPLATE, "slug": slug, "name": slug.replace("-", " ").title()}
    (pack_dir / "pack.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (pack_dir / "kpis" / "total_revenue.json").write_text(
        json.dumps(_EXAMPLE_KPI, indent=2) + "\n", encoding="utf-8"
    )
    return pack_dir


# --------------------------------------------------------------------------- #
# test against a dataset
# --------------------------------------------------------------------------- #


@dataclass
class RoleBindReport:
    role: str
    bound_to: str | None
    table_alias: str | None
    confidence: float
    source: str | None


@dataclass
class PackTestReport:
    pack_slug: str
    dataset: str
    match_confidence: float
    total_roles: int
    unresolved_roles: list[str]
    role_binds: list[RoleBindReport]
    compiled_kpis: list[str]
    skipped_kpis: list[str]
    dry_run: list[tuple[str, str]] = field(default_factory=list)  # (kpi_id, "12 rows" | "ERROR: ...")

    @property
    def bound_roles(self) -> int:
        return self.total_roles - len(self.unresolved_roles)

    @property
    def bind_rate(self) -> float:
        return self.bound_roles / self.total_roles if self.total_roles else 1.0


def run_pack_test(pack_dir: Path, dataset: Path) -> PackTestReport:
    """The full deterministic path: ingest -> profile -> match -> bind ->
    compile -> dry-run, for one pack against one dataset. No LLM."""
    pack = load_pack(pack_dir)
    ds = ingest(dataset)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)

    match = score_pack(pack, extract_signature(profile))
    bindings = resolve_bindings(profile, pack)  # provider=None -> deterministic only

    role_binds: list[RoleBindReport] = []
    for role in sorted(pack.canonical_roles):
        cb = bindings.column(role)
        role_binds.append(
            RoleBindReport(
                role=role,
                bound_to=cb.physical if cb else None,
                table_alias=cb.table_alias if cb else None,
                confidence=cb.confidence if cb else 0.0,
                source=cb.source if cb else None,
            )
        )

    kpi_defs = compile_all(pack, bindings)
    compiled_ids = [k.id for k in kpi_defs.kpis]

    dry_run: list[tuple[str, str]] = []
    if kpi_defs.kpis:
        con = open_session(ds)
        try:
            for kpi in kpi_defs.kpis:
                try:
                    n = len(con.execute(kpi.sql).fetchdf())
                    dry_run.append((kpi.id, f"{n} row(s)"))
                except Exception as exc:  # noqa: BLE001 - report, don't abort the whole test
                    dry_run.append((kpi.id, f"ERROR: {exc}"))
        finally:
            con.close()

    return PackTestReport(
        pack_slug=pack.slug,
        dataset=str(dataset),
        match_confidence=match.confidence,
        total_roles=len(pack.canonical_roles),
        unresolved_roles=list(bindings.unresolved_roles),
        role_binds=role_binds,
        compiled_kpis=compiled_ids,
        skipped_kpis=list(kpi_defs.skipped),
        dry_run=dry_run,
    )

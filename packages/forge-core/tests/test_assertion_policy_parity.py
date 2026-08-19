from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_core.validation.assertion_policy import (
    _allowed_call_names,
    _allowed_node_types,
    AssertionPolicyError,
    evaluate_assertion,
    validate_assertion,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_whitelists_match_runtime_copy() -> None:
    """forge-core and mcp-runtime each carry a copy of the assertion policy
    (deliberate package independence). This test pins them together so the
    two whitelists cannot silently drift."""
    from mis_mcp_runtime.engine.assertions import (
        _allowed_call_names as runtime_call_names,
    )
    from mis_mcp_runtime.engine.assertions import (
        _allowed_node_types as runtime_node_types,
    )

    assert _allowed_node_types() == runtime_node_types()
    assert _allowed_call_names() == runtime_call_names()


def test_evaluate_assertion_works() -> None:
    assert evaluate_assertion("total >= 0 and total <= 100", {"total": 50}) is True
    assert evaluate_assertion("abs(delta) < 10", {"delta": -3}) is True


def test_unknown_name_raises() -> None:
    with pytest.raises(AssertionPolicyError):
        evaluate_assertion("nope > 0", {"total": 1})


def test_all_real_pack_assertions_pass_validation() -> None:
    """Every assertion shipped in industry-packs/*/kpis/*.json must be
    accepted by the policy — guards against a future pack author writing
    something the runtime will reject."""
    assertions = []
    for kpi_file in (REPO_ROOT / "industry-packs").rglob("kpis/*.json"):
        data = json.loads(kpi_file.read_text(encoding="utf-8"))
        for assertion in data.get("assertions", []):
            assertions.append((kpi_file.name, assertion))

    assert assertions, "expected at least one pack assertion to exist"
    for source, assertion in assertions:
        validate_assertion(assertion), f"{source}: {assertion!r}"
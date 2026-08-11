from __future__ import annotations

from pathlib import Path

from forge_core.models.common import CheckStatus
from forge_core.validation.plugin_spec import check_plugin_spec

GOLDEN_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "golden"


def test_none_plugin_dir_is_skipped():
    result = check_plugin_spec(None)
    assert result.status == CheckStatus.SKIPPED


def test_hand_verified_golden_plugin_passes():
    plugin_dir = GOLDEN_ROOT / "_spec-test" / "plugins" / "curelo-bookings-poc"
    result = check_plugin_spec(plugin_dir)
    assert result.status == CheckStatus.PASS, result.issues


def test_missing_manifest_fails():
    result = check_plugin_spec(GOLDEN_ROOT)  # no .claude-plugin/plugin.json directly here
    assert result.status == CheckStatus.FAIL

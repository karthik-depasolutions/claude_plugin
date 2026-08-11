"""Stage 6 - validation harness. See `forge_core.validation.harness.run_harness`
for the entry point and `docs/architecture.md` §5 for why each of the eight
checks (`forge_core.models.validation.CHECK_NAMES`) exists.
"""

from __future__ import annotations

from forge_core.validation.harness import run_harness

__all__ = ["run_harness"]

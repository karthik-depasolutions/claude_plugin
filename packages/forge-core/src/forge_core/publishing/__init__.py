"""Stage 8 - publishing. Three strategies for getting a packaged plugin to a
customer: `local` (copy/zip to a path), `github` (push to a customer-specific
repo), `marketplace` (add to a central marketplace catalog)."""

from __future__ import annotations

from forge_core.publishing.github import get_repo, push_plugin_to_repo
from forge_core.publishing.local import publish_local
from forge_core.publishing.marketplace import publish_to_marketplace

__all__ = ["get_repo", "publish_local", "publish_to_marketplace", "push_plugin_to_repo"]

"""Stage 8 - publishing. Four strategies for getting a packaged plugin to a
customer: `local` (copy/zip to a path), `github` (push to an *existing*
customer-specific repo), `marketplace` (add to a central marketplace
catalog), `standalone_repo` (create a brand-new, self-installable repo for
one plugin - no pre-existing repo or marketplace required)."""

from __future__ import annotations

from forge_core.publishing.github import get_repo, push_plugin_to_repo
from forge_core.publishing.local import publish_local
from forge_core.publishing.marketplace import publish_to_marketplace
from forge_core.publishing.standalone_repo import (
    PublishedRepo,
    create_repo,
    publish_plugin_as_new_repo,
    slugify_repo_name,
)

__all__ = [
    "PublishedRepo",
    "create_repo",
    "get_repo",
    "publish_local",
    "publish_plugin_as_new_repo",
    "publish_to_marketplace",
    "push_plugin_to_repo",
    "slugify_repo_name",
]

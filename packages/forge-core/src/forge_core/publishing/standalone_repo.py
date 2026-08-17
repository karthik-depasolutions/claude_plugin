"""One GitHub repo per plugin, created on demand and self-installable on its
own - no separate marketplace repo required first.

Composes two things that already exist rather than inventing a fourth
publishing shape: `publish_to_marketplace` (wraps the plugin in the
`plugins/<name>/` + `.claude-plugin/marketplace.json` layout Claude Code's
`/plugin marketplace add` expects) and `github.push_plugin_to_repo` (commits
a whole directory in one Git Data API call). The only genuinely new piece
is creating the repo itself, auto-initialized so `push_plugin_to_repo`'s
`heads/<branch>` ref lookup has something to find.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge_core.models.plugin_spec import MarketplaceOwner, PluginManifest
from forge_core.publishing.github import push_plugin_to_repo
from forge_core.publishing.marketplace import publish_to_marketplace

_SLUG_INVALID = re.compile(r"[^a-z0-9-]+")
_VERSION_SUFFIX = re.compile(r"-v(\d+)$")
DEFAULT_BRANCH = "main"
MAX_NAME_ATTEMPTS = 50


def slugify_repo_name(name: str) -> str:
    """GitHub repo names tolerate more than this, but `MarketplacePluginEntry.
    name` (the catalog entry we also use as the repo name) is constrained to
    `^[a-z][a-z0-9-]*$` - slugify once, up front, so both stay valid."""
    slug = _SLUG_INVALID.sub("-", name.lower()).strip("-")
    return slug or "mis-plugin"


def versioned_repo_names(requested: str, *, limit: int = MAX_NAME_ATTEMPTS) -> list[str]:
    """`foo`, then `foo-v1`, `foo-v2`, ... If `requested` is already `foo-v3`,
    keep going from there (`foo-v3`, `foo-v4`, ...) so we never produce
    `foo-v3-v1`."""
    match = _VERSION_SUFFIX.search(requested)
    if match:
        base = requested[: match.start()]
        start = int(match.group(1))
        names = [requested, *[f"{base}-v{n}" for n in range(start + 1, start + limit)]]
    else:
        names = [requested, *[f"{requested}-v{n}" for n in range(1, limit)]]
    return names[:limit]


@dataclass
class PublishedRepo:
    repo_full_name: str
    html_url: str
    clone_url: str
    plugin_name: str
    marketplace_add_command: str
    install_command: str


def _already_exists(exc: BaseException) -> bool:
    status = getattr(exc, "status", None)
    if status == 422:
        return True
    return "already exists" in str(exc).lower()


def create_repo(
    token: str,
    name: str,
    *,
    owner: str | None = None,
    private: bool = False,
    description: str | None = None,
) -> Any:
    """Creates a new GitHub repo. If `name` is already taken, creates
    `{name}-v1`, then `-v2`, and so on, rather than overwriting the
    existing repo or failing with 422.

    `owner` is tried as an organization first; if it isn't an org the token
    can create repos in, we fall back to the token's own account."""
    from github import Auth, Github, GithubException

    client = Github(auth=Auth.Token(token))
    user: Any = client.get_user()
    description = description or ""
    candidates = versioned_repo_names(name)

    def _get(full_name: str) -> Any | None:
        try:
            return client.get_repo(full_name)
        except GithubException as exc:
            if getattr(exc, "status", None) != 404:
                raise
            return None

    def _create(factory: Any, owner_login: str) -> Any:
        for candidate in candidates:
            if _get(f"{owner_login}/{candidate}") is not None:
                continue
            try:
                return factory.create_repo(
                    candidate, private=private, description=description, auto_init=True
                )
            except GithubException as exc:
                if _already_exists(exc):
                    continue
                raise
        raise RuntimeError(
            f"could not find a free GitHub repo name after {len(candidates)} "
            f"attempts starting from {name!r}"
        )

    if owner:
        try:
            org = client.get_organization(owner)
        except GithubException:
            return _create(user, user.login)
        return _create(org, owner)

    return _create(user, user.login)


def _required_credential_env_vars(plugin_dir: Path) -> list[str]:
    """A live-database-sourced plugin (customer's own DB, or a schema loaded
    into the client warehouse - see forge_core.ingestion.warehouse) never
    bundles a `data/` folder; instead `config/data_source.json`'s
    `duckdb_attach_sql` references a `${VAR}` placeholder the runtime
    resolves from the environment at connect time. Reading it back here (no
    model import needed - it's just JSON) lets the README name that variable
    without ever having seen the credential itself."""
    data_source_path = plugin_dir / "config" / "data_source.json"
    if not data_source_path.exists():
        return []
    data = json.loads(data_source_path.read_text(encoding="utf-8"))
    attach_sql = " ".join(data.get("connection", {}).get("duckdb_attach_sql", []))
    return sorted(set(re.findall(r"\$\{(\w+)\}", attach_sql)))


def _standalone_readme(
    manifest: PluginManifest, repo_full_name: str, catalog_name: str, plugin_dir: Path
) -> str:
    env_vars = _required_credential_env_vars(plugin_dir)
    setup_section = ""
    if env_vars:
        exports = "\n".join(f'export {var}="<connection string given to you separately>"' for var in env_vars)
        setup_section = (
            "\n## Before first use\n\n"
            "This plugin reads live from a database - there is no bundled `data/` folder. "
            f"Set the following in the environment Claude Desktop/Code launches from, "
            "using the connection string you were given when this plugin was generated "
            "(it is never stored in this repo):\n\n"
            f"```bash\n{exports}\n```\n"
        )
    return (
        f"# {manifest.displayName or manifest.name}\n\n"
        f"{manifest.description or ''}\n\n"
        "Generated by [MIS Plugin Forge](https://github.com/) — a Claude Code/Desktop plugin, "
        "ready to install directly from this repo.\n\n"
        "## Install in Claude Code or Claude Desktop\n\n"
        "```text\n"
        f"/plugin marketplace add {repo_full_name}\n"
        f"/plugin install {manifest.name}@{catalog_name}\n"
        "/reload-plugins\n"
        "```\n"
        f"{setup_section}\n"
        f"Plugin version `{manifest.version}`. See `plugins/{manifest.name}/README.md` for the KPIs "
        "and setup notes bundled with this specific plugin.\n"
    )


def publish_plugin_as_new_repo(
    plugin_dir: Path,
    *,
    token: str,
    repo_name: str | None = None,
    owner: str | None = None,
    private: bool = False,
) -> PublishedRepo:
    """Package `plugin_dir` (already validated - see forge_core.validation)
    into a GitHub repo that any Claude Code/Desktop user can install from
    with nothing but the two commands this returns. If a repo of that name
    already exists, a new one is created as `{name}-v1` (then `-v2`, ...)
    instead of overwriting."""
    from forge_core.validation.plugin_spec import load_manifest

    manifest, issues = load_manifest(plugin_dir)
    if manifest is None:
        raise ValueError(f"cannot publish an invalid plugin: {[i.message for i in issues]}")

    requested_name = slugify_repo_name(repo_name or manifest.name)
    repo = create_repo(
        token, requested_name, owner=owner, private=private, description=manifest.description
    )
    catalog_name = repo.name
    repo_full_name = repo.full_name

    with tempfile.TemporaryDirectory() as tmp:
        marketplace_dir = Path(tmp) / catalog_name
        publish_to_marketplace(
            plugin_dir,
            marketplace_dir,
            marketplace_name=catalog_name,
            owner=MarketplaceOwner(name=owner or "MIS Plugin Forge"),
            marketplace_description=manifest.description,
        )

        readme = _standalone_readme(manifest, repo_full_name, catalog_name, plugin_dir)
        (marketplace_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")

        push_plugin_to_repo(
            repo,
            marketplace_dir,
            branch=DEFAULT_BRANCH,
            commit_message=f"Publish {manifest.name} v{manifest.version}",
            repo_full_name=repo_full_name,
        )

    return PublishedRepo(
        repo_full_name=repo_full_name,
        html_url=repo.html_url,
        clone_url=repo.clone_url,
        plugin_name=manifest.name,
        marketplace_add_command=f"/plugin marketplace add {repo_full_name}",
        install_command=f"/plugin install {manifest.name}@{catalog_name}",
    )


__all__ = [
    "PublishedRepo",
    "create_repo",
    "publish_plugin_as_new_repo",
    "slugify_repo_name",
    "versioned_repo_names",
]

"""Customer-specific GitHub repo publishing strategy.

Pushes a packaged plugin directory as a single commit using the Git Data
API (blob/tree/commit, not one REST call per file), so a plugin with
dozens of generated files still publishes in a handful of requests. The
GitHub interaction is expressed against `GitHubRepoLike` - a minimal
Protocol matching the handful of PyGithub `Repository` methods actually
used - so this can be unit-tested with an in-memory fake instead of hitting
the real API.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Protocol

from forge_core.models.plugin_spec import GithubSource


class GitHubRepoLike(Protocol):
    def get_git_ref(self, ref: str) -> Any: ...
    def get_git_commit(self, sha: str) -> Any: ...
    def create_git_blob(self, content: str, encoding: str) -> Any: ...
    def create_git_tree(self, elements: list[Any], base_tree: Any = None) -> Any: ...
    def create_git_commit(self, message: str, tree: Any, parents: list[Any]) -> Any: ...


def _iter_plugin_files(plugin_dir: Path) -> list[Path]:
    return sorted(p for p in plugin_dir.rglob("*") if p.is_file())


def push_plugin_to_repo(
    repo: GitHubRepoLike,
    plugin_dir: Path,
    *,
    branch: str = "main",
    commit_message: str = "Update generated plugin",
    repo_full_name: str = "",
) -> GithubSource:
    """Commit every file under `plugin_dir` to `branch` in one commit.

    `repo` must behave like a PyGithub `Repository` (see `GitHubRepoLike`).
    Returns a `GithubSource` a marketplace catalog entry can point at.
    """
    from github import InputGitTreeElement  # lazy import - only needed for real pushes

    ref = repo.get_git_ref(f"heads/{branch}")
    base_commit = repo.get_git_commit(ref.object.sha)

    tree_elements = []
    for file_path in _iter_plugin_files(plugin_dir):
        rel_path = file_path.relative_to(plugin_dir).as_posix()
        content_b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
        blob = repo.create_git_blob(content_b64, "base64")
        tree_elements.append(InputGitTreeElement(path=rel_path, mode="100644", type="blob", sha=blob.sha))

    new_tree = repo.create_git_tree(tree_elements, base_tree=base_commit.tree)
    new_commit = repo.create_git_commit(commit_message, new_tree, [base_commit])
    ref.edit(new_commit.sha)

    return GithubSource(repo=repo_full_name, ref=branch, sha=new_commit.sha)


def get_repo(token: str, repo_full_name: str) -> GitHubRepoLike:
    """Thin wrapper so callers don't need to import PyGithub directly."""
    from github import Auth, Github

    client = Github(auth=Auth.Token(token))
    return client.get_repo(repo_full_name)

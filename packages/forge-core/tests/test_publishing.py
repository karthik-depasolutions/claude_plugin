from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.models.plugin_spec import MarketplaceOwner
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.profiling import build_structural_only
from forge_core.publishing.github import push_plugin_to_repo
from forge_core.publishing.local import publish_local
from forge_core.publishing.marketplace import publish_to_marketplace

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _packaged_plugin(source_path: Path, pack_slug: str, tmp_path: Path, name: str) -> Path:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)
    pack = load_pack(PACKS_ROOT / pack_slug)
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)
    generated = generate_plugin_content(pack, kpi_defs, profile.source, provider=None)
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)

    plugin_dir = tmp_path / name
    write_plugin(
        spec, plugin_dir, source=profile.source, profile=profile, pack=pack, bundle_mcp_runtime=False
    )
    return plugin_dir


def test_publish_local_copy(bookings_csv: Path, tmp_path: Path):
    plugin_dir = _packaged_plugin(bookings_csv, "healthcare-diagnostics", tmp_path, "plugin-src")
    destination = tmp_path / "installed"

    result = publish_local(plugin_dir, destination)

    assert result == destination / plugin_dir.name
    assert (result / ".claude-plugin" / "plugin.json").is_file()


def test_publish_local_zip(bookings_csv: Path, tmp_path: Path):
    plugin_dir = _packaged_plugin(bookings_csv, "healthcare-diagnostics", tmp_path, "plugin-src")
    zip_path = publish_local(plugin_dir, tmp_path / "dist" / "plugin.zip", as_zip=True)
    assert zip_path.is_file()


def test_publish_to_marketplace_creates_catalog(bookings_csv: Path, tmp_path: Path):
    plugin_dir = _packaged_plugin(
        bookings_csv, "healthcare-diagnostics", tmp_path, "healthcare-diagnostics-mis-plugin"
    )
    marketplace_dir = tmp_path / "marketplace"

    publish_to_marketplace(
        plugin_dir,
        marketplace_dir,
        marketplace_name="acme-mis-marketplace",
        owner=MarketplaceOwner(name="Acme"),
    )

    catalog_path = marketplace_dir / ".claude-plugin" / "marketplace.json"
    assert catalog_path.is_file()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["name"] == "acme-mis-marketplace"
    plugin_names = {p["name"] for p in catalog["plugins"]}
    assert "healthcare-diagnostics-mis-plugin" in plugin_names
    # The marketplace entry must never also set version (plan §4).
    assert all("version" not in p for p in catalog["plugins"])
    assert (marketplace_dir / "plugins" / "healthcare-diagnostics-mis-plugin" / ".mcp.json").exists()


def test_publish_to_marketplace_preserves_previously_published_plugins(
    bookings_csv: Path, retail_orders_dir: Path, tmp_path: Path
):
    marketplace_dir = tmp_path / "marketplace"
    owner = MarketplaceOwner(name="Acme")

    first = _packaged_plugin(
        bookings_csv, "healthcare-diagnostics", tmp_path, "healthcare-diagnostics-mis-plugin"
    )
    publish_to_marketplace(first, marketplace_dir, marketplace_name="acme-mis-marketplace", owner=owner)

    second = _packaged_plugin(
        retail_orders_dir, "retail-ecommerce", tmp_path, "retail-ecommerce-mis-plugin"
    )
    publish_to_marketplace(second, marketplace_dir, marketplace_name="acme-mis-marketplace", owner=owner)

    catalog = json.loads(
        (marketplace_dir / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    plugin_names = {p["name"] for p in catalog["plugins"]}
    assert plugin_names == {"healthcare-diagnostics-mis-plugin", "retail-ecommerce-mis-plugin"}


class _FakeGitObject:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class _FakeRef:
    def __init__(self, sha: str) -> None:
        self.object = _FakeGitObject(sha)
        self.edited_to: str | None = None

    def edit(self, sha: str) -> None:
        self.edited_to = sha


class _FakeCommit:
    def __init__(self, sha: str, tree_sha: str) -> None:
        self.sha = sha
        self.tree = _FakeGitObject(tree_sha)


class _FakeRepo:
    """Minimal stand-in for a PyGithub Repository, tracking calls so the test
    can assert push_plugin_to_repo() drives the Git Data API correctly
    without touching the network."""

    def __init__(self) -> None:
        self.ref = _FakeRef("base-commit-sha")
        self.blobs_created: list[str] = []
        self.tree_elements: list[Any] = []
        self.commit_message: str | None = None

    def get_git_ref(self, ref: str) -> _FakeRef:
        assert ref == "heads/main"
        return self.ref

    def get_git_commit(self, sha: str) -> _FakeCommit:
        assert sha == "base-commit-sha"
        return _FakeCommit(sha, "base-tree-sha")

    def create_git_blob(self, content: str, encoding: str) -> _FakeGitObject:
        assert encoding == "base64"
        self.blobs_created.append(content)
        return _FakeGitObject(f"blob-sha-{len(self.blobs_created)}")

    def create_git_tree(self, elements: list[Any], base_tree: Any = None) -> _FakeGitObject:
        assert base_tree.sha == "base-tree-sha"
        self.tree_elements = elements
        return _FakeGitObject("new-tree-sha")

    def create_git_commit(self, message: str, tree: Any, parents: list[Any]) -> _FakeCommit:
        self.commit_message = message
        assert tree.sha == "new-tree-sha"
        assert parents[0].sha == "base-commit-sha"
        return _FakeCommit("a" * 40, tree.sha)


def test_push_plugin_to_repo_commits_every_file_in_one_commit(bookings_csv: Path, tmp_path: Path):
    plugin_dir = _packaged_plugin(bookings_csv, "healthcare-diagnostics", tmp_path, "plugin")
    fake_repo = _FakeRepo()

    source = push_plugin_to_repo(
        fake_repo, plugin_dir, repo_full_name="acme/healthcare-diagnostics-mis-plugin"
    )

    expected_file_count = sum(1 for p in plugin_dir.rglob("*") if p.is_file())
    assert len(fake_repo.blobs_created) == expected_file_count
    assert len(fake_repo.tree_elements) == expected_file_count
    assert fake_repo.ref.edited_to == "a" * 40
    assert source.repo == "acme/healthcare-diagnostics-mis-plugin"
    assert source.ref == "main"
    assert source.sha == "a" * 40

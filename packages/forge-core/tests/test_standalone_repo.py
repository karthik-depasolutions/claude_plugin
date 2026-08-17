from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.profiling import build_structural_only
from forge_core.publishing import standalone_repo
from forge_core.publishing.standalone_repo import (
    _already_exists,
    _required_credential_env_vars,
    _standalone_readme,
    publish_plugin_as_new_repo,
    slugify_repo_name,
    versioned_repo_names,
)

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("healthcare-diagnostics-mis-plugin", "healthcare-diagnostics-mis-plugin"),
        ("Acme Corp Plugin!", "acme-corp-plugin"),
        ("  ---  ", "mis-plugin"),
    ],
)
def test_slugify_repo_name(raw: str, expected: str):
    assert slugify_repo_name(raw) == expected


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
    """Minimal stand-in for a freshly `create_repo`'d PyGithub Repository -
    auto_init=True means it already has one commit on `main`, which is what
    push_plugin_to_repo's `heads/main` ref lookup requires."""

    full_name = "acme/healthcare-diagnostics-mis-plugin"
    html_url = "https://github.com/acme/healthcare-diagnostics-mis-plugin"
    clone_url = "https://github.com/acme/healthcare-diagnostics-mis-plugin.git"

    @property
    def name(self) -> str:
        return self.full_name.rsplit("/", 1)[-1]

    def __init__(self) -> None:
        self.ref = _FakeRef("base-commit-sha")
        self.tree_elements: list[Any] = []
        self.commit_message: str | None = None
        self.blob_content_by_sha: dict[str, str] = {}

    def get_git_ref(self, ref: str) -> _FakeRef:
        assert ref == "heads/main"
        return self.ref

    def get_git_commit(self, sha: str) -> _FakeCommit:
        return _FakeCommit(sha, "base-tree-sha")

    def create_git_blob(self, content: str, encoding: str) -> _FakeGitObject:
        assert encoding == "base64"
        sha = f"blob-sha-{len(self.blob_content_by_sha)}"
        self.blob_content_by_sha[sha] = content
        return _FakeGitObject(sha)

    def create_git_tree(self, elements: list[Any], base_tree: Any = None) -> _FakeGitObject:
        assert base_tree.sha == "base-tree-sha"
        self.tree_elements = elements
        return _FakeGitObject("new-tree-sha")

    def create_git_commit(self, message: str, tree: Any, parents: list[Any]) -> _FakeCommit:
        self.commit_message = message
        return _FakeCommit("a" * 40, tree.sha)


def test_required_credential_env_vars_is_empty_for_a_plugin_with_no_data_source_json(tmp_path: Path):
    assert _required_credential_env_vars(tmp_path) == []


def test_required_credential_env_vars_is_empty_for_a_file_backed_plugin(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "data_source.json").write_text(
        json.dumps({"connection": {"duckdb_attach_sql": ["CREATE VIEW src_orders AS SELECT * FROM "
                                                          "read_csv_auto('{DATA_DIR}/orders.csv')"]}}),
        encoding="utf-8",
    )
    assert _required_credential_env_vars(tmp_path) == []


def test_required_credential_env_vars_finds_the_placeholder_for_a_live_db_plugin(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "data_source.json").write_text(
        json.dumps(
            {
                "connection": {
                    "duckdb_attach_sql": [
                        "INSTALL postgres; LOAD postgres;",
                        "ATTACH '${FORGE_SOURCE_DB_URL}' AS srcdb (TYPE POSTGRES, READ_ONLY)",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    assert _required_credential_env_vars(tmp_path) == ["FORGE_SOURCE_DB_URL"]


def test_standalone_readme_omits_setup_section_for_a_file_backed_plugin(tmp_path: Path):
    from forge_core.models.plugin_spec import PluginManifest

    manifest = PluginManifest(name="acme-mis-plugin")
    readme = _standalone_readme(manifest, "acme/acme-mis-plugin", "acme-mis-plugin", tmp_path)
    assert "Before first use" not in readme


def test_standalone_readme_includes_setup_section_naming_the_env_var_for_a_live_db_plugin(tmp_path: Path):
    from forge_core.models.plugin_spec import PluginManifest

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    attach_sql = ["ATTACH '${FORGE_SOURCE_DB_URL}' AS srcdb (TYPE POSTGRES)"]
    (config_dir / "data_source.json").write_text(
        json.dumps({"connection": {"duckdb_attach_sql": attach_sql}}),
        encoding="utf-8",
    )
    manifest = PluginManifest(name="acme-mis-plugin")
    readme = _standalone_readme(manifest, "acme/acme-mis-plugin", "acme-mis-plugin", tmp_path)
    assert "Before first use" in readme
    assert "export FORGE_SOURCE_DB_URL=" in readme
    # The whole point: never print an actual credential in a public repo's README.
    assert "postgresql://" not in readme


def test_publish_plugin_as_new_repo(bookings_csv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugin_dir = _packaged_plugin(
        bookings_csv, "healthcare-diagnostics", tmp_path, "healthcare-diagnostics-mis-plugin"
    )

    fake_repo = _FakeRepo()
    create_repo_calls: list[dict[str, Any]] = []

    def fake_create_repo(token: str, name: str, **kwargs: Any) -> _FakeRepo:
        create_repo_calls.append({"token": token, "name": name, **kwargs})
        return fake_repo

    monkeypatch.setattr(standalone_repo, "create_repo", fake_create_repo)

    result = publish_plugin_as_new_repo(plugin_dir, token="fake-token", owner="acme")

    assert len(create_repo_calls) == 1
    call = create_repo_calls[0]
    assert call["token"] == "fake-token"
    assert call["name"] == "healthcare-diagnostics-mis-plugin"
    assert call["owner"] == "acme"
    assert call["private"] is False
    assert result.repo_full_name == "acme/healthcare-diagnostics-mis-plugin"
    assert result.html_url == fake_repo.html_url
    assert result.plugin_name == "healthcare-diagnostics-mis-plugin"
    assert result.marketplace_add_command == "/plugin marketplace add acme/healthcare-diagnostics-mis-plugin"
    assert (
        result.install_command
        == "/plugin install healthcare-diagnostics-mis-plugin@healthcare-diagnostics-mis-plugin"
    )

    # The pushed tree must contain the marketplace-wrapped plugin (under
    # plugins/<name>/) plus a root README with the install instructions -
    # not just the raw plugin directory. InputGitTreeElement only exposes
    # its fields via the (package-private but stable) `_identity` dict.
    elements_by_path = {el._identity["path"]: el._identity for el in fake_repo.tree_elements}
    assert "README.md" in elements_by_path
    assert ".claude-plugin/marketplace.json" in elements_by_path
    assert "plugins/healthcare-diagnostics-mis-plugin/.claude-plugin/plugin.json" in elements_by_path

    import base64

    readme_content = base64.b64decode(
        fake_repo.blob_content_by_sha[elements_by_path["README.md"]["sha"]]
    ).decode("utf-8")
    assert result.marketplace_add_command in readme_content
    assert result.install_command in readme_content

    catalog_content = json.loads(
        base64.b64decode(
            fake_repo.blob_content_by_sha[elements_by_path[".claude-plugin/marketplace.json"]["sha"]]
        ).decode("utf-8")
    )
    assert catalog_content["name"] == "healthcare-diagnostics-mis-plugin"
    assert {p["name"] for p in catalog_content["plugins"]} == {"healthcare-diagnostics-mis-plugin"}


def test_publish_plugin_as_new_repo_uses_uniquified_github_name_as_catalog(
    bookings_csv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    plugin_dir = _packaged_plugin(
        bookings_csv, "healthcare-diagnostics", tmp_path, "healthcare-diagnostics-mis-plugin"
    )
    fake_repo = _FakeRepo()
    fake_repo.full_name = "acme/healthcare-diagnostics-mis-plugin-v1"
    fake_repo.html_url = "https://github.com/acme/healthcare-diagnostics-mis-plugin-v1"
    fake_repo.clone_url = fake_repo.html_url + ".git"

    monkeypatch.setattr(standalone_repo, "create_repo", lambda *a, **k: fake_repo)

    result = publish_plugin_as_new_repo(plugin_dir, token="fake-token", owner="acme")

    assert result.repo_full_name == "acme/healthcare-diagnostics-mis-plugin-v1"
    assert result.marketplace_add_command == (
        "/plugin marketplace add acme/healthcare-diagnostics-mis-plugin-v1"
    )
    assert result.install_command == (
        "/plugin install healthcare-diagnostics-mis-plugin@healthcare-diagnostics-mis-plugin-v1"
    )
    import base64

    elements_by_path = {el._identity["path"]: el._identity for el in fake_repo.tree_elements}
    catalog_content = json.loads(
        base64.b64decode(
            fake_repo.blob_content_by_sha[elements_by_path[".claude-plugin/marketplace.json"]["sha"]]
        ).decode("utf-8")
    )
    assert catalog_content["name"] == "healthcare-diagnostics-mis-plugin-v1"


class _FakeGithubException(Exception):
    def __init__(self, status: int, message: str = "Not Found") -> None:
        self.status = status
        super().__init__(message)


class _FakeAuth:
    class Token:
        def __init__(self, token: str) -> None:
            self.token = token


def test_already_exists_detects_github_422():
    assert _already_exists(_FakeGithubException(422, "name already exists on this account"))
    assert not _already_exists(_FakeGithubException(404, "Not Found"))
    assert not _already_exists(_FakeGithubException(500, "server error"))


def test_versioned_repo_names_appends_v1_then_v2():
    names = versioned_repo_names("generic-analytics-mis-plugin", limit=4)
    assert names == [
        "generic-analytics-mis-plugin",
        "generic-analytics-mis-plugin-v1",
        "generic-analytics-mis-plugin-v2",
        "generic-analytics-mis-plugin-v3",
    ]


def test_versioned_repo_names_increments_an_existing_v_suffix():
    assert versioned_repo_names("generic-analytics-mis-plugin-v1", limit=3) == [
        "generic-analytics-mis-plugin-v1",
        "generic-analytics-mis-plugin-v2",
        "generic-analytics-mis-plugin-v3",
    ]


def test_create_repo_appends_v1_when_the_name_is_taken(monkeypatch: pytest.MonkeyPatch):
    created: list[str] = []
    taken = {"karthik/generic-analytics-mis-plugin"}

    class _CreatedRepo(_FakeRepo):
        full_name = "karthik/generic-analytics-mis-plugin-v1"
        html_url = "https://github.com/karthik/generic-analytics-mis-plugin-v1"
        clone_url = "https://github.com/karthik/generic-analytics-mis-plugin-v1.git"

    class _FakeUser:
        login = "karthik"

        def create_repo(self, name: str, **kwargs: Any) -> _CreatedRepo:
            created.append(name)
            return _CreatedRepo()

    class _FakeGithub:
        def __init__(self, auth: Any = None) -> None:
            pass

        def get_user(self) -> _FakeUser:
            return _FakeUser()

        def get_repo(self, full_name: str) -> _FakeRepo:
            if full_name in taken:
                return _FakeRepo()
            raise _FakeGithubException(404)

        def get_organization(self, owner: str) -> Any:
            raise _FakeGithubException(404)

    import github as github_mod

    monkeypatch.setattr(github_mod, "Github", _FakeGithub)
    monkeypatch.setattr(github_mod, "Auth", _FakeAuth)
    monkeypatch.setattr(github_mod, "GithubException", _FakeGithubException)

    repo = standalone_repo.create_repo("fake-token", "generic-analytics-mis-plugin")

    assert created == ["generic-analytics-mis-plugin-v1"]
    assert repo.name == "generic-analytics-mis-plugin-v1"


def test_create_repo_falls_back_to_user_account_and_still_uniquifies(
    monkeypatch: pytest.MonkeyPatch,
):
    created: list[str] = []
    taken = {"karthik/generic-analytics-mis-plugin"}

    class _CreatedRepo(_FakeRepo):
        full_name = "karthik/generic-analytics-mis-plugin-v1"

    class _FakeUser:
        login = "karthik"

        def create_repo(self, name: str, **kwargs: Any) -> _CreatedRepo:
            created.append(name)
            return _CreatedRepo()

    class _FakeGithub:
        def __init__(self, auth: Any = None) -> None:
            pass

        def get_user(self) -> _FakeUser:
            return _FakeUser()

        def get_repo(self, full_name: str) -> _FakeRepo:
            if full_name in taken:
                return _FakeRepo()
            raise _FakeGithubException(404)

        def get_organization(self, owner: str) -> Any:
            raise _FakeGithubException(404)

    import github as github_mod

    monkeypatch.setattr(github_mod, "Github", _FakeGithub)
    monkeypatch.setattr(github_mod, "Auth", _FakeAuth)
    monkeypatch.setattr(github_mod, "GithubException", _FakeGithubException)

    repo = standalone_repo.create_repo(
        "fake-token", "generic-analytics-mis-plugin", owner="acme"
    )

    assert created == ["generic-analytics-mis-plugin-v1"]
    assert repo.name == "generic-analytics-mis-plugin-v1"

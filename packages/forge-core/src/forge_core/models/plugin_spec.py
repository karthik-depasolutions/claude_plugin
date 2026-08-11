"""In-memory models of the actual Claude Code / Claude Desktop plugin file
formats, verified against `claude` CLI v2.1.201 (see docs/plugin-format.md).

These models exist so the packager builds structurally-correct manifests by
construction rather than by string formatting. Every constraint noted here
was empirically verified — getting one wrong makes a generated plugin fail
`claude plugin validate` or, worse, fail silently at install time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_component_path(v: str) -> str:
    if not (v == "./" or v.startswith("./")):
        raise ValueError(f"component path must be relative and start with './': {v!r}")
    if ".." in v:
        raise ValueError(f"component path must not contain '..': {v!r}")
    return v


class Author(BaseModel):
    """MUST be an object. `"author": "Some Name"` is a hard validation error."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str | None = None
    url: str | None = None


class UserConfigOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "number", "boolean", "directory", "file"]
    title: str
    description: str
    sensitive: bool = False
    required: bool = False
    default: str | int | bool | None = None


class PluginManifest(BaseModel):
    """`.claude-plugin/plugin.json`. Only `name` is required; everything else
    defaults to auto-discovery of the conventional directories."""

    model_config = ConfigDict(extra="forbid")

    schema_: str | None = Field(default=None, alias="$schema")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    displayName: str | None = None
    version: str | None = None
    description: str | None = None
    author: Author | None = None
    homepage: str | None = None
    repository: str | None = None  # MUST be a string, not an object.
    license: str | None = None
    keywords: list[str] = Field(default_factory=list)
    defaultEnabled: bool = True

    skills: str | list[str] | None = None
    commands: str | list[str] | None = None
    agents: str | list[str] | None = None
    hooks: str | None = None
    mcpServers: str | None = None

    userConfig: dict[str, UserConfigOption] = Field(default_factory=dict)

    @field_validator("skills", "commands", "agents", "hooks", "mcpServers")
    @classmethod
    def _check_paths(cls, v: str | list[str] | None) -> str | list[str] | None:
        if v is None:
            return v
        if isinstance(v, list):
            return [_validate_component_path(p) for p in v]
        return _validate_component_path(v)

    def to_json_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, exclude_defaults=False)


class GithubSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["github"] = "github"
    repo: str
    ref: str | None = None
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")


class GitUrlSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["url"] = "url"
    url: str
    ref: str | None = None
    sha: str | None = None


class GitSubdirSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["git-subdir"] = "git-subdir"
    url: str
    path: str
    ref: str | None = None
    sha: str | None = None


class NpmSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["npm"] = "npm"
    package: str
    version: str | None = None
    registry: str | None = None


class ArchiveSource(BaseModel):
    """Requires target Claude Code >= v2.1.224 — see docs/plugin-format.md."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["archive"] = "archive"
    url: str
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


RemoteSource = GithubSource | GitUrlSource | GitSubdirSource | NpmSource | ArchiveSource


class MarketplacePluginEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    source: str | RemoteSource
    description: str | None = None
    version: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    strict: bool = True


_RESERVED_MARKETPLACE_NAMES = {
    "claude-code-marketplace",
    "claude-code-plugins",
    "claude-plugins-official",
    "claude-plugins-community",
    "claude-community",
    "anthropic-marketplace",
    "anthropic-plugins",
    "agent-skills",
    "anthropic-agent-skills",
    "knowledge-work-plugins",
    "life-sciences",
    "claude-for-legal",
    "claude-for-financial-services",
    "financial-services-plugins",
    "first-party-plugins",
    "healthcare",
    "org",
    "org-provisioned",
    "unknown",
}


class MarketplaceOwner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str | None = None
    url: str | None = None


class MarketplaceManifest(BaseModel):
    """`.claude-plugin/marketplace.json`."""

    model_config = ConfigDict(extra="forbid")

    schema_: str | None = Field(default=None, alias="$schema")
    name: str
    owner: MarketplaceOwner
    description: str | None = None
    version: str | None = None
    plugins: list[MarketplacePluginEntry]

    @field_validator("name")
    @classmethod
    def _reject_reserved(cls, v: str) -> str:
        if v in _RESERVED_MARKETPLACE_NAMES:
            raise ValueError(f"{v!r} is a reserved marketplace name")
        return v

    def to_json_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


class McpStdioServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpRemoteServer(BaseModel):
    """URL-based servers MUST declare `type` explicitly — a typeless entry
    with a `url` is a documented runtime trap (read as stdio and skipped)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "sse", "ws"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


McpServerEntry = McpStdioServer | McpRemoteServer


class McpConfig(BaseModel):
    """`.mcp.json`."""

    model_config = ConfigDict(extra="forbid")

    mcpServers: dict[str, McpServerEntry]

    def to_json_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


PORTABLE_SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _split_tool_list(v: str | list[str] | None) -> list[str] | None:
    """Claude Code accepts `tools`/`allowed-tools` as either a YAML list or a
    single comma-separated string; normalize to a list on the way in so both
    hand-authored and generator-authored frontmatter validate the same way."""
    if isinstance(v, str):
        return [t.strip() for t in v.split(",") if t.strip()]
    return v


class SkillFrontmatter(BaseModel):
    """SKILL.md frontmatter, restricted to the six fields portable across
    Claude Code, Claude Desktop, Cowork, and the hosted Skills API."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(max_length=64, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    description: str = Field(max_length=1024)
    license: str | None = None
    compatibility: str | None = Field(default=None, max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list, alias="allowed-tools")

    _split_tools = field_validator("allowed_tools", mode="before")(_split_tool_list)

    def to_frontmatter_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)


class AgentFrontmatter(BaseModel):
    """agents/*.md frontmatter. `hooks`, `mcpServers`, `permissionMode` are
    intentionally NOT modeled here — Claude Code silently ignores them on
    plugin-shipped agents, so the generator must never emit them."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    description: str
    tools: list[str] = Field(default_factory=list)
    disallowedTools: list[str] = Field(default_factory=list)
    model: Literal["sonnet", "opus", "haiku", "inherit"] = "inherit"
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    maxTurns: int | None = None
    skills: list[str] = Field(default_factory=list)
    color: Literal[
        "red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"
    ] | None = None

    _split_tools = field_validator("tools", "disallowedTools", mode="before")(_split_tool_list)

    def to_frontmatter_dict(self) -> dict:
        d = self.model_dump(exclude_none=True, exclude_defaults=True)
        if "tools" in d:
            d["tools"] = ", ".join(d["tools"])
        if "disallowedTools" in d:
            d["disallowedTools"] = ", ".join(d["disallowedTools"])
        return d


class CommandFrontmatter(BaseModel):
    """commands/*.md frontmatter."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    description: str
    argument_hint: str | None = Field(default=None, alias="argument-hint")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowed-tools")
    model: str | None = None

    _split_tools = field_validator("allowed_tools", mode="before")(_split_tool_list)

    def to_frontmatter_dict(self) -> dict:
        d = self.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
        if "allowed-tools" in d:
            d["allowed-tools"] = ", ".join(d["allowed-tools"])
        return d


class HookHandler(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["command", "http", "mcp_tool", "prompt", "agent"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    server: str | None = None
    tool: str | None = None
    prompt: str | None = None
    timeout: int | None = None
    status_message: str | None = Field(default=None, alias="statusMessage")
    condition: str | None = Field(default=None, alias="if")
    run_async: bool | None = Field(default=None, alias="async")


class HookMatcherGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matcher: str = "*"
    hooks: list[HookHandler]


class HooksFile(BaseModel):
    """hooks/hooks.json. Keys are hook event names, validated against the
    documented enum by the plugin-spec validation check, not here (keeping
    this model forward-compatible with new event names)."""

    model_config = ConfigDict(extra="forbid")

    hooks: dict[str, list[HookMatcherGroup]] = Field(default_factory=dict)

    def to_json_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)


class GeneratedFile(BaseModel):
    """One file the packager will write, relative to the plugin root."""

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    content: str
    is_json: bool = False


class PluginSpec(BaseModel):
    """The complete in-memory plugin, ready to hand to
    forge_core.packaging.plugin_builder. Every field here has already been
    through the generation stage; packaging only serializes and validates."""

    model_config = ConfigDict(extra="forbid")

    manifest: PluginManifest
    mcp_config: McpConfig | None = None
    hooks: HooksFile | None = None
    files: list[GeneratedFile] = Field(default_factory=list)
    readme: str = ""

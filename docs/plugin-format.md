# Plugin format — the spec constraints the packager enforces

Verified against `claude` CLI **v2.1.201**. Every rule below is enforced
either by construction (a Pydantic model in
[`forge_core/models/plugin_spec.py`](../packages/forge-core/src/forge_core/models/plugin_spec.py)
that simply can't represent the invalid shape) or by the `plugin_spec`
check in the [validation harness](security.md), and cross-checked for real
by the `cli_validate` check shelling out to `claude plugin validate --strict`.
`fixtures/golden/_spec-test/` is a hand-verified-valid plugin kept as a
fixture specifically to catch regressions in this enforcement.

- **BOM-less UTF-8** for every JSON/Markdown file. A BOM produces
  `Invalid JSON syntax: Unrecognized token`. `packaging/plugin_builder.py`
  writes every file with `encoding="utf-8"` and no BOM, never
  `utf-8-sig`.
- `author` in `plugin.json` is an **object** (`{name, email?, url?}`);
  `repository` is a **string**. (`PluginManifest` in `plugin_spec.py`.)
- Every component path declared in `plugin.json` must **exist**, be
  **relative**, and **start with `./`** (`"./"`, not `"."`, for a skills
  directory). Never emit a key without writing its files —
  `plugin_builder.write_plugin` only writes a manifest key when the
  corresponding content was actually generated.
- Only `plugin.json` lives in `.claude-plugin/`. `skills/`, `agents/`,
  `commands/`, `hooks/`, `.mcp.json` sit at the plugin root.
- **Skill frontmatter** is restricted to the six portable Agent Skills
  fields: `name`, `description`, `license`, `compatibility`, `metadata`,
  `allowed-tools` (`SkillFrontmatter`, `extra="forbid"`). `name` ≤ 64
  chars, kebab-case, and must equal its directory name; `description` ≤
  1024 chars (enforced in `generation/skills.py` when building the
  description).
- **Agent frontmatter** must omit `hooks`, `mcpServers`, `permissionMode`
  (silently ignored for plugin agents — `AgentFrontmatter` doesn't define
  these fields at all, so they can't be set). `model` is one of
  `sonnet|opus|haiku|inherit`.
- Every MCP server entry needs an explicit `type`. Bundled paths use
  `${CLAUDE_PLUGIN_ROOT}`; anything that must survive plugin updates uses
  `${CLAUDE_PLUGIN_DATA}`.
- Set `version` in **either** `plugin.json` or the marketplace entry, never
  both. Reserved marketplace names (`healthcare`, `anthropic-plugins`,
  `first-party-plugins`, ...) are rejected by
  `MarketplaceManifest` validation.
- `archive` marketplace sources are only emitted when explicitly
  requested; the default is a `github` or relative-path source, since
  `archive` needs CLI ≥ 2.1.224.

## Generated plugin layout

```text
<pack-slug>-mis-plugin/
├── .claude-plugin/plugin.json
├── .mcp.json                    # points at the bundled mis-mcp-runtime
├── skills/<pack-slug>-analyst/SKILL.md
├── agents/<pack-slug>-deep-dive.md
├── commands/<pack-slug>-report.md
├── hooks/hooks.json              # SessionStart guardrail reminder
├── artifacts/dashboard.html      # static KPI snapshot, real computed values
├── config/
│   ├── schema_bindings.json      # canonical role -> physical table.column
│   └── kpi_defs.json             # compiled, sqlglot-validated SQL
├── data/                         # copied source files the runtime queries
└── mcp_server/                   # bundled mis-mcp-runtime + requirements.txt
```

See [generator-flow.md](generator-flow.md) for how each of these gets
produced, and [security.md](security.md) for what stops an invalid or
unsafe one from ever reaching a customer.

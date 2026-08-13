# Marketplace

A marketplace is a directory (usually a Git repo) with a
`.claude-plugin/marketplace.json` catalog and a `plugins/` folder — the
format the `claude` CLI's `/plugin marketplace add` expects. Forge builds
and maintains one via `forge_core.packaging.marketplace_builder` and
`forge_core.publishing.marketplace`.

## Catalog format

`build_marketplace_manifest(name, owner, plugin_manifests, description=...)`
(`packaging/marketplace_builder.py`) produces a `MarketplaceManifest`
(`models/plugin_spec.py`) — validated the same way `plugin.json` is, so a
malformed catalog can't be written. Constraints beyond the plugin-format
ones in [plugin-format.md](plugin-format.md):

- Reserved names (`healthcare`, `anthropic-plugins`, `first-party-plugins`,
  ...) are rejected.
- `version` lives on **either** the plugin's own `plugin.json` or its
  marketplace entry — never both.
- Sources are `github` or a relative path by default; an `archive` source
  is only emitted when explicitly requested (needs CLI ≥ 2.1.224).

## Publishing strategies (`forge_core.publishing`)

| Strategy | Function | Use case |
|---|---|---|
| Local | `local.publish_local` | Copy or zip a packaged plugin to a path — the default for dev and for customers who install from a local checkout. |
| GitHub (existing repo) | `github.push_plugin_to_repo` | Push a packaged plugin as a single commit (via the Git Data API — blob/tree/commit, not one REST call per file) to a customer-specific repo that already exists. |
| Marketplace | `marketplace.publish_to_marketplace` | Copy into a marketplace checkout's `plugins/<name>/` and rewrite its catalog, merging with whatever's already published there. |
| Standalone repo | `standalone_repo.publish_plugin_as_new_repo` | One click, right after a run succeeds: creates a **brand-new** GitHub repo, wraps the plugin in a marketplace-shaped catalog so the repo is self-installable, and pushes it — no pre-existing repo or marketplace needed. This is what the web wizard's "Publish to GitHub" button and `forge publish github` call. |

`local`, `marketplace`, and `github` (standalone) are exposed via `forge
publish local|marketplace|github` (see `forge publish --help`); pushing a
marketplace checkout to GitHub afterwards is `forge publish marketplace
... --push` (reuses the "existing repo" GitHub strategy against the
marketplace directory itself).

## One-click publish (standalone repo)

After a run succeeds, `POST /runs/{run_id}/publish/github` (or the web
wizard's "Publish to GitHub" button, or `forge publish github <plugin_dir>`)
does all of the following in one step:

1. Wraps the packaged plugin in the same `plugins/<name>/` +
   `.claude-plugin/marketplace.json` shape `publish_to_marketplace` uses, so
   the repo is a valid marketplace on its own.
2. Writes a root `README.md` with the exact install commands for the repo
   that's about to be created.
3. Creates a new GitHub repo (under `owner`, or `GITHUB_ORG`, or the
   token's own account if neither is set) via `GITHUB_TOKEN`, auto-initialized
   so it already has a `main` branch to push onto.
4. Pushes everything as one commit.

The response (and the generated README) gives the caller the two commands
needed to install it anywhere:

```text
/plugin marketplace add <owner>/<repo>
/plugin install <plugin-name>@<repo>
/reload-plugins
```

If the plugin reads from a live database (a customer-supplied connection, or
one loaded into the client warehouse — see [customer-installation.md](customer-installation.md#plugins-generated-from-a-live-database)),
the README also gets a "Before first use" section naming the env var to set
(`FORGE_SOURCE_DB_URL`) — never the credential itself, which this repo (public
by default) never sees.

See [customer-installation.md](customer-installation.md) for what happens
after that, from the installer's point of view.

## Publishing to a marketplace, end to end

```bash
# 1. Generate the plugin
uv run forge run fixtures/datasets/bookings.csv --pack healthcare-diagnostics --out generated/release

# 2. Add it to a local marketplace checkout's catalog
uv run forge publish marketplace \
  generated/release/healthcare-diagnostics-mis-plugin ./marketplace-checkout \
  --marketplace-name mis-plugins --owner-name "Your Org"

# 3. Push that checkout to the marketplace's GitHub repo in one commit
uv run forge publish marketplace \
  generated/release/healthcare-diagnostics-mis-plugin ./marketplace-checkout \
  --marketplace-name mis-plugins --owner-name "Your Org" --push
# requires GITHUB_TOKEN and FORGE_MARKETPLACE_REPO in the environment
```

See [.github/workflows/release.yml](../.github/workflows/release.yml) for
how this runs in CI on a version tag.

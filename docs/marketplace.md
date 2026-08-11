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
| GitHub | `github.push_plugin_to_repo` | Push a packaged plugin as a single commit (via the Git Data API — blob/tree/commit, not one REST call per file) to a customer-specific repo. |
| Marketplace | `marketplace.publish_to_marketplace` | Copy into a marketplace checkout's `plugins/<name>/` and rewrite its catalog, merging with whatever's already published there. |

All three are exposed via `forge publish local|marketplace` (see `forge
publish --help`); pushing a marketplace checkout to GitHub afterwards is
`forge publish marketplace ... --push` (reuses the GitHub strategy against
the marketplace directory itself).

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

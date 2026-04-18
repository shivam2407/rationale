# Changelog

All notable changes to Rationale are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] — 2026-04-18

### Added

- **Claude Code plugin packaging.** The repo is now directly installable
  as a Claude Code plugin: `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, `.mcp.json`, `hooks/hooks.json`,
  and five slash commands under `commands/`. After
  `pip install rationale` (so the CLI is on `PATH`), users can run
  `/plugin marketplace add shivam2407/rationale` and
  `/plugin install rationale@rationale` inside Claude Code to get the
  Stop hook + MCP server + `/why`, `/rationale-check`,
  `/rationale-summary`, `/rationale-graph`, and `/rationale-export`
  wired up for them.
- Plugin manifest test suite (`tests/test_plugin_manifests.py`) that
  pins the validator rules: `plugin.json` must not declare `hooks`,
  version fields across `plugin.json` / `marketplace.json` / the Python
  package must match, slash-command files must carry a `description:`
  frontmatter entry, each slash command must document the
  `pip install rationale` prerequisite, the Stop hook must pass
  `--quiet`, and the Stop hook command must degrade gracefully when
  the `rationale` CLI isn't on `PATH`.

### Changed

- The Stop hook now appends `|| true` to the capture command so a
  session end never fails visibly inside Claude Code just because the
  user installed the plugin before running `pip install rationale`.

## [0.3.0] — 2026-04-18

The v2 milestone described in
[`rationale-architecture.md`](rationale-architecture.md) (lines 143-147).

### Added

- **Decision graph** (`rationale graph`). Surfaces two edge kinds:
  `SUPERSEDES` when a newer decision picks a different option on the
  same symbol (the closest thing to "this was walked back") and
  `RELATED` when decisions share a symbol or overlap line ranges.
  Edge direction for SUPERSEDES is always newer → older.
- **Confidence-weighted rollups** (`rationale summary`). Aggregates
  decisions by file, agent, and tag, weighted by confidence
  (high=1.0, medium=0.6, low=0.25). Intended for tech leads who need
  to see where deliberation is concentrated without reading every
  individual decision.
- **EU AI Act JSON-LD export** (`rationale export`). Produces a
  standalone JSON-LD document with a stable `@context` URL, generator
  metadata, and the complete decision record. Optional HMAC-SHA256
  signing via `--sign` (requires `RATIONALE_SIGNING_KEY`); optional
  Ed25519 signing via `--sign --ed25519` behind the new `[crypto]`
  extra. Signatures are computed over canonical JSON (sorted keys,
  compact separators) so verification is reproducible.
- **Cross-agent MCP server** (`rationale mcp`). Exposes
  `rationale_why`, `rationale_list`, `rationale_check`, and
  `rationale_summary` as Model Context Protocol tools so any MCP-aware
  coding agent (Claude Desktop, Cursor, etc.) can query the decision
  log without re-ingesting the repo. Implements a narrow JSON-RPC 2.0
  subset that is compatible with MCP clients; the full `mcp` SDK is
  optional via the `[mcp]` extra.
- New `[crypto]` and `[mcp]` extras so the core install stays tiny.

### Notes

- The architecture spec also lists "team sync server" under v2. Since
  team sync inherently requires a network service and a deployment
  story, it is tracked as a post-0.3 effort rather than shipped in
  this release.

## [0.2.0] — 2026-04-17

First public release. The v1 milestone described in
[`rationale-architecture.md`](rationale-architecture.md).

### Added

- **Symbolic anchoring.** Decisions now record the enclosing symbol
  (function or class) along with the line range. When a refactor moves
  the block, `rationale why` can still find it by symbol name rather
  than dead line numbers.
- **Content fingerprints.** Each anchor stores a SHA-256 hash of the
  anchored source, normalized to ignore trailing whitespace. This is
  the foundation for staleness detection.
- **Staleness detector** (`rationale check`). Classifies each decision
  as FRESH, DRIFTED (block moved but unchanged), STALE (body changed),
  MISSING (file or symbol gone), or UNKNOWN (legacy v0 anchor with no
  hash). Exits 1 when STALE or MISSING decisions are found, so it can
  gate CI.
- **Machine-readable staleness output** (`rationale check --json`).
  Intended for editor integrations and dashboard tooling.
- **`why` output** now shows the anchored symbol name when available.
- Python 3.13 added to the supported matrix.
- Packaging metadata: rich classifiers, documentation/source/changelog
  URLs, and `build` + `twine` in the dev extras for publishing.

### Changed

- `DecisionAnchor` gained optional `symbol` and `content_hash` fields.
  Decisions written by v0.1 keep loading unchanged — the new fields
  default to `None` and the staleness detector reports them as UNKNOWN.
- Development Status classifier moved from Alpha to Beta.

### Notes

- Tree-sitter-based anchoring is still on the roadmap. The v1 symbol
  extractor uses Python's stdlib `ast` for `.py` and narrow regex
  extractors for JS/TS, Go, and Rust — good enough for top-level
  functions and classes, without a 100MB native dependency.
- Editor integrations (VS Code CodeLens, Cursor extension) remain on
  the v2 roadmap as separate TypeScript packages.

## [0.1.0] — 2026-04-16

Initial v0 scope from the architecture spec.

### Added

- Claude Code Stop-hook capture pipeline.
- Haiku-based distiller with an offline heuristic fallback.
- Line-range anchoring with drift tolerance.
- `rationale init`, `capture`, `why`, `list`, and `install-hook`
  commands, plus a bare `why` shortcut.
- Local-first `.rationale/YYYY-MM/d-<hash>.md` storage with YAML
  frontmatter.

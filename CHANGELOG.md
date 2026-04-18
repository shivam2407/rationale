# Changelog

All notable changes to Rationale are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-04-17

First public release. This is the v1 milestone described in
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

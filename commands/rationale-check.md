---
description: Classify every decision FRESH / DRIFTED / STALE / MISSING / UNKNOWN vs. the current working tree. Exits 1 on STALE or MISSING — suitable for CI.
---

# /rationale-check

Walk every entry in `.rationale/` and compare its content hash + symbol anchor against the current working tree.

## Statuses

| Status | Meaning |
|---|---|
| FRESH | Content at the stored line range still matches. |
| DRIFTED | Block moved but content is unchanged. Still valid. |
| STALE | Symbol still exists but the body changed — review the rationale. |
| MISSING | File or symbol is gone. |
| UNKNOWN | Pre-v1 anchor with no content hash. Can't classify. |

## What this runs

```bash
rationale check "$ARGUMENTS"
```

Pass `--json` for machine-readable output or `--all` to include FRESH/DRIFTED rows.

Requires the `rationale` CLI (`pip install rationale`) to be on `PATH`.

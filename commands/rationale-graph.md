---
description: Print the decision relationship graph. SUPERSEDES (newer walked back older) and RELATED (shared symbol or overlapping anchors).
---

# /rationale-graph

Surface two edge kinds across the decision log:

- `SUPERSEDES`: a newer decision picked a different option on the same symbol as an older decision. The closest signal we have for "this was walked back".
- `RELATED`: shared symbol with the same choice, or overlapping line ranges on the same file.

Edge direction for `SUPERSEDES` is always newer → older.

## What this runs

```bash
rationale graph "$ARGUMENTS"
```

Pass `--json` to get a `{nodes, edges}` payload suitable for feeding into a downstream tool.

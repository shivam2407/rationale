---
description: Confidence-weighted rollups of the decision log by file, agent, and tag. Shows where deliberation is concentrated.
---

# /rationale-summary

Print (or emit as JSON) a weighted summary of every captured decision:

- Overall scorecard: total count, per-confidence counts, weighted score (high=1.0, medium=0.6, low=0.25)
- Top N by file
- Top N by agent
- Top N by tag

Useful for tech leads who want to see *where* the AI has been making the most consequential calls without reading every individual decision.

## What this runs

```bash
rationale summary "$ARGUMENTS"
```

Supports `--json`, `--top N`, and `--path <repo>`.

Requires the `rationale` CLI (`pip install rationale-cli`) to be on `PATH`.

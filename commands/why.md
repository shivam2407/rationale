---
description: Look up the reasoning behind a line, file, or search term in the rationale decision log.
---

# /why

Query the repo-local `.rationale/` decision log to recover why a past agent session chose what it chose.

## Usage

- `/why src/payment.py:42` — decisions anchored to that line (with drift tolerance)
- `/why src/payment.py` — every decision touching that file
- `/why "retry"` — free-text search across decision bodies

## What this runs

```bash
rationale why "$ARGUMENTS"
```

The `rationale` CLI must be on `PATH`. Install with `pip install rationale-cli` if it isn't already.

Add `--json` for machine-readable output when another agent needs to parse the result.

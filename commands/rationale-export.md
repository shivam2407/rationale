---
description: Export the decision log as JSON-LD for EU AI Act provenance disclosure. Optional HMAC-SHA256 or Ed25519 signing.
---

# /rationale-export

Write a JSON-LD export of every captured decision with a stable `@context` URL, generator metadata, and (optionally) a cryptographic proof block.

## Flags

- `--output path.jsonld` — where to write. Defaults to `.rationale/export.jsonld`.
- `--sign` — attach an HMAC-SHA256 proof. Requires the `RATIONALE_SIGNING_KEY` environment variable to be set to the HMAC secret.
- `--sign --ed25519` — asymmetric Ed25519 signing instead. Requires the `[crypto]` extra (`pip install "rationale[crypto]"`) and `RATIONALE_SIGNING_KEY` to point at a PEM-encoded private key file.

Signatures cover canonical JSON (sorted keys, compact separators) so verification is reproducible across platforms.

## What this runs

```bash
rationale export "$ARGUMENTS"
```

Requires the `rationale` CLI (`pip install rationale`) to be on `PATH`.

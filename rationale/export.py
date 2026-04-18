"""EU AI Act provenance export.

The EU AI Act (applicable August 2026 for general-purpose AI) requires
operators of high-risk AI systems to document the reasoning behind
AI-generated artifacts. Rationale's decision log is exactly that record,
but it lives as repo-local markdown. For an auditor we serialize it to
JSON-LD with a stable @context URL so machine consumers know how to
interpret the fields.

Signing:
- HMAC-SHA256 (stdlib only) gives a fast integrity check when a shared
  secret is acceptable (internal audits, CI provenance).
- Public-key signing (Ed25519) lives behind the optional ``[crypto]``
  extra; when cryptography is available, ``write_export(..., ed25519=True)``
  writes an asymmetric signature instead. This is the shape external
  auditors need.

The signature covers the entire document minus the ``proof`` field so
consumers can drop ``proof`` and recompute the MAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rationale import __version__
from rationale.models import Decision

# Stable context URI. The schema itself ships with the package, but we
# publish it under a permanent URL so JSON-LD consumers can resolve it.
CONTEXT_URL = "https://rationale.dev/context/v1.jsonld"

SIGNING_ENV_VAR = "RATIONALE_SIGNING_KEY"


def build_export(decisions: Iterable[Decision]) -> dict[str, Any]:
    """Serialize decisions to a JSON-LD compatible dict."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision_list = [_decision_to_jsonld(d) for d in decisions]
    return {
        "@context": CONTEXT_URL,
        "@type": "DecisionLog",
        "generator": {
            "name": "rationale",
            "version": __version__,
        },
        "generatedAt": now,
        "regulatoryContext": "EU-AI-Act",
        "decisions": decision_list,
    }


def write_export(
    decisions: Iterable[Decision],
    path: Path | str,
    *,
    sign: bool = False,
    ed25519: bool = False,
) -> Path:
    """Render a JSON-LD export to ``path``.

    When ``sign`` is true, an HMAC-SHA256 proof is embedded using the key
    from the ``RATIONALE_SIGNING_KEY`` environment variable. When
    ``ed25519`` is also true, the ``cryptography`` extra is required and
    a PEM-encoded Ed25519 private key is expected at the env var path.
    """
    doc = build_export(list(decisions))

    if sign:
        if ed25519:
            sig = _ed25519_sign(doc)
            doc["proof"] = {
                "type": "Ed25519Signature2020",
                "created": doc["generatedAt"],
                "signatureValue": sig,
            }
        else:
            key = _require_signing_key()
            sig = hmac_sign(doc, key=key)
            doc["proof"] = {
                "type": "HmacSha256",
                "created": doc["generatedAt"],
                "signatureValue": sig,
            }

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    return target


def hmac_sign(doc: dict[str, Any], *, key: bytes) -> str:
    """Compute an HMAC-SHA256 signature of the canonical JSON form of ``doc``.

    The document is serialized with sorted keys and no whitespace so the
    signature is reproducible across platforms.
    """
    payload = _canonical_json(doc)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_hmac_signature(
    doc: dict[str, Any], signature: str, *, key: bytes
) -> bool:
    """Constant-time verification of an HMAC signature."""
    expected = hmac_sign(doc, key=key)
    # hmac.compare_digest protects against timing attacks on signature comparison
    return hmac.compare_digest(expected, signature)


# --- Internal helpers -------------------------------------------------------


def _decision_to_jsonld(d: Decision) -> dict[str, Any]:
    return {
        "@id": f"rationale:{d.id}",
        "@type": "Decision",
        "timestamp": d.timestamp,
        "agent": d.agent,
        "gitSha": d.git_sha,
        "sessionId": d.session_id,
        "chosen": d.chosen,
        "alternativesConsidered": list(d.alternatives),
        "confidence": d.confidence,
        "tags": list(d.tags),
        "reasoning": d.reasoning,
        "anchors": [
            {
                "file": a.file,
                "lineStart": a.line_start,
                "lineEnd": a.line_end,
                "symbol": a.symbol,
                "contentHash": a.content_hash,
            }
            for a in d.anchors
        ],
    }


def _canonical_json(doc: dict[str, Any]) -> bytes:
    """Canonicalize for signing: sorted keys, compact, UTF-8."""
    return json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _require_signing_key() -> bytes:
    raw = os.environ.get(SIGNING_ENV_VAR)
    if not raw:
        raise RuntimeError(
            f"{SIGNING_ENV_VAR} is not set; cannot sign export. "
            "Set the env var to an HMAC secret before passing --sign."
        )
    return raw.encode("utf-8")


def _ed25519_sign(doc: dict[str, Any]) -> str:
    """Sign with Ed25519 using the optional ``cryptography`` extra.

    Expects the env var ``RATIONALE_SIGNING_KEY`` to be a file path to
    a PEM-encoded Ed25519 private key. The public key is not embedded
    in the proof; the verifier supplies it out of band.

    Raises :class:`RuntimeError` when the key is not an Ed25519 private
    key — a silent success with an RSA/EC key would contradict the
    ``Ed25519Signature2020`` type written into the proof block.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key,
        )
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "Ed25519 signing requires the [crypto] extra: "
            "pip install 'rationale[crypto]'."
        ) from exc

    key_path = os.environ.get(SIGNING_ENV_VAR)
    if not key_path:
        raise RuntimeError(
            f"{SIGNING_ENV_VAR} must point to a PEM-encoded Ed25519 private "
            "key file when ed25519=True."
        )
    with open(key_path, "rb") as f:
        private_key = load_pem_private_key(f.read(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError(
            "RATIONALE_SIGNING_KEY must point to an Ed25519 PEM key; "
            f"got {type(private_key).__name__}. Refusing to emit a proof "
            "claiming Ed25519Signature2020 with a non-Ed25519 signature."
        )
    signature = private_key.sign(_canonical_json(doc))
    return signature.hex()

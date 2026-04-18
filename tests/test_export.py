"""Tests for EU AI Act JSON-LD export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rationale.export import (
    CONTEXT_URL,
    build_export,
    hmac_sign,
    verify_hmac_signature,
    write_export,
)
from rationale.models import Decision, DecisionAnchor


def _d(did: str = "d-1", chosen: str = "x") -> Decision:
    return Decision(
        id=did,
        timestamp="2026-04-17T00:00:00Z",
        agent="claude-code",
        chosen=chosen,
        reasoning="why we did it",
        anchors=[
            DecisionAnchor(
                file="src/payment.py",
                line_start=1,
                line_end=10,
                symbol="PaymentService.retry",
                content_hash="abc123",
            )
        ],
        tags=["reliability"],
        git_sha="deadbeef",
    )


def test_build_export_contains_jsonld_context() -> None:
    out = build_export([_d()])
    assert out["@context"] == CONTEXT_URL
    assert out["@type"] == "DecisionLog"


def test_build_export_serializes_decisions() -> None:
    out = build_export([_d("d-a"), _d("d-b")])
    assert len(out["decisions"]) == 2
    first = out["decisions"][0]
    assert first["@id"] == "rationale:d-a"
    assert first["chosen"] == "x"
    assert first["reasoning"].startswith("why")
    assert first["anchors"][0]["symbol"] == "PaymentService.retry"
    assert first["anchors"][0]["contentHash"] == "abc123"


def test_build_export_records_generation_metadata() -> None:
    out = build_export([_d()])
    assert "generatedAt" in out
    assert out["generator"]["name"] == "rationale"
    assert "version" in out["generator"]
    assert out["regulatoryContext"] == "EU-AI-Act"


def test_build_export_empty_decision_list_is_valid() -> None:
    out = build_export([])
    assert out["decisions"] == []
    assert out["@context"] == CONTEXT_URL


def test_write_export_round_trips_json(tmp_path: Path) -> None:
    path = tmp_path / "export.jsonld"
    write_export([_d()], path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["@type"] == "DecisionLog"
    assert parsed["decisions"][0]["@id"] == "rationale:d-1"


def test_hmac_sign_is_deterministic() -> None:
    doc = build_export([_d()])
    sig_1 = hmac_sign(doc, key=b"secret-key")
    sig_2 = hmac_sign(doc, key=b"secret-key")
    assert sig_1 == sig_2
    assert len(sig_1) == 64  # hex of sha256 → 64 chars


def test_hmac_sign_changes_when_content_changes() -> None:
    a = build_export([_d("d-a")])
    b = build_export([_d("d-b")])
    assert hmac_sign(a, key=b"k") != hmac_sign(b, key=b"k")


def test_hmac_sign_changes_when_key_changes() -> None:
    doc = build_export([_d()])
    assert hmac_sign(doc, key=b"k1") != hmac_sign(doc, key=b"k2")


def test_verify_hmac_signature_accepts_valid() -> None:
    doc = build_export([_d()])
    sig = hmac_sign(doc, key=b"k")
    assert verify_hmac_signature(doc, sig, key=b"k")


def test_verify_hmac_signature_rejects_invalid() -> None:
    doc = build_export([_d()])
    sig = hmac_sign(doc, key=b"k")
    assert not verify_hmac_signature(doc, sig, key=b"wrong-key")


def test_verify_hmac_signature_rejects_tampered_document() -> None:
    doc = build_export([_d()])
    sig = hmac_sign(doc, key=b"k")
    doc["decisions"][0]["chosen"] = "tampered"
    assert not verify_hmac_signature(doc, sig, key=b"k")


def test_write_export_with_signing_embeds_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATIONALE_SIGNING_KEY", "super-secret")
    path = tmp_path / "signed.jsonld"
    write_export([_d()], path, sign=True)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert "proof" in parsed
    assert parsed["proof"]["type"] == "HmacSha256"
    # Signature must verify against the exported document minus the proof
    sig = parsed["proof"]["signatureValue"]
    unsigned = {k: v for k, v in parsed.items() if k != "proof"}
    assert verify_hmac_signature(unsigned, sig, key=b"super-secret")


def test_write_export_signing_without_key_raises(tmp_path: Path) -> None:
    # No RATIONALE_SIGNING_KEY in env — signing must fail loudly rather
    # than silently emit an unsigned file while claiming it's signed.
    import os

    saved = os.environ.pop("RATIONALE_SIGNING_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            write_export([_d()], tmp_path / "x.jsonld", sign=True)
    finally:
        if saved is not None:
            os.environ["RATIONALE_SIGNING_KEY"] = saved


# --- Ed25519 signing tests --------------------------------------------------


def _write_ed25519_key(tmp_path: Path) -> Path:
    """Generate an Ed25519 private key on disk in PEM form."""
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "signing.pem"
    key_path.write_bytes(pem)
    return key_path


def test_ed25519_export_embeds_signature_of_expected_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = _write_ed25519_key(tmp_path)
    monkeypatch.setenv("RATIONALE_SIGNING_KEY", str(key_path))
    out = tmp_path / "signed.jsonld"
    write_export([_d()], out, sign=True, ed25519=True)

    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["proof"]["type"] == "Ed25519Signature2020"
    sig_hex = parsed["proof"]["signatureValue"]
    # Ed25519 signatures are 64 bytes → 128 hex chars.
    assert len(sig_hex) == 128


def test_ed25519_export_verifies_with_public_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("cryptography")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    key_path = _write_ed25519_key(tmp_path)
    pem_bytes = key_path.read_bytes()
    private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    public_key = private_key.public_key()

    monkeypatch.setenv("RATIONALE_SIGNING_KEY", str(key_path))
    out = tmp_path / "signed.jsonld"
    write_export([_d()], out, sign=True, ed25519=True)

    parsed = json.loads(out.read_text(encoding="utf-8"))
    sig = bytes.fromhex(parsed["proof"]["signatureValue"])
    unsigned = {k: v for k, v in parsed.items() if k != "proof"}

    from rationale.export import _canonical_json

    payload = _canonical_json(unsigned)
    public_key.verify(sig, payload)  # raises InvalidSignature if bad

    # Tamper with the document → verification must fail
    tampered = {**unsigned}
    tampered["decisions"] = [{**unsigned["decisions"][0], "chosen": "HACKED"}]
    with pytest.raises(InvalidSignature):
        public_key.verify(sig, _canonical_json(tampered))


def test_ed25519_refuses_non_ed25519_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the sign path must detect RSA/EC keys and refuse to
    emit a proof block that claims Ed25519Signature2020."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "rsa.pem"
    key_path.write_bytes(pem)
    monkeypatch.setenv("RATIONALE_SIGNING_KEY", str(key_path))

    with pytest.raises(RuntimeError, match="Ed25519"):
        write_export(
            [_d()],
            tmp_path / "should-not-exist.jsonld",
            sign=True,
            ed25519=True,
        )

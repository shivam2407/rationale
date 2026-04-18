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

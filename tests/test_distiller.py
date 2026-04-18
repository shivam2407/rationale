"""Tests for the distillation layer (LLM-injected and offline)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pytest

from rationale.capture import FileEdit, SessionTrace
from rationale.distiller import Distiller, _parse_json_array


@dataclass
class FakeClient:
    response: str
    calls: int = 0

    def complete(self, system: str, user: str, model: str) -> str:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        self.last_model = model
        return self.response


def test_distill_empty_trace_returns_empty(empty_trace: SessionTrace) -> None:
    d = Distiller(client=FakeClient(response="[]"))
    assert d.distill(empty_trace) == []


def test_distill_uses_injected_client_and_returns_decisions(
    trace_with_edits: SessionTrace,
) -> None:
    payload = json.dumps(
        [
            {
                "chosen": "fixed 3x retry",
                "alternatives": ["exponential_backoff", "circuit_breaker"],
                "reasoning": "Downstream rate limits already cap traffic.",
                "files": ["src/payment.ts"],
                "confidence": "medium",
                "tags": ["reliability"],
            }
        ]
    )
    client = FakeClient(response=payload)
    distiller = Distiller(client=client, git_sha="abc1234")
    decisions = distiller.distill(trace_with_edits)
    assert client.calls == 1
    assert len(decisions) == 1
    d = decisions[0]
    assert d.chosen == "fixed 3x retry"
    assert d.alternatives == ["exponential_backoff", "circuit_breaker"]
    assert d.git_sha == "abc1234"
    assert d.session_id == "sess-edits"
    assert len(d.anchors) == 1
    assert d.anchors[0].file == "src/payment.ts"
    assert d.anchors[0].line_start == 42
    assert d.anchors[0].line_end == 58


def test_distill_caps_at_max_decisions(trace_with_edits: SessionTrace) -> None:
    items = [
        {
            "chosen": f"choice-{i}",
            "reasoning": "because",
            "files": ["src/payment.ts"],
        }
        for i in range(50)
    ]
    distiller = Distiller(client=FakeClient(response=json.dumps(items)))
    decisions = distiller.distill(trace_with_edits)
    assert len(decisions) == 20


def test_distill_skips_items_missing_chosen_or_reasoning(
    trace_with_edits: SessionTrace,
) -> None:
    payload = json.dumps(
        [
            {"chosen": "", "reasoning": "x"},
            {"chosen": "x", "reasoning": ""},
            {"chosen": "good", "reasoning": "valid", "files": []},
        ]
    )
    decisions = Distiller(client=FakeClient(response=payload)).distill(
        trace_with_edits
    )
    assert [d.chosen for d in decisions] == ["good"]


def test_distill_normalizes_bad_confidence(
    trace_with_edits: SessionTrace,
) -> None:
    payload = json.dumps(
        [
            {
                "chosen": "x",
                "reasoning": "y",
                "confidence": "absolute",
                "files": ["src/payment.ts"],
            }
        ]
    )
    decisions = Distiller(client=FakeClient(response=payload)).distill(
        trace_with_edits
    )
    assert decisions[0].confidence == "medium"


def test_distill_handles_garbage_response(
    trace_with_edits: SessionTrace,
) -> None:
    """A garbage LLM response must not lose the session — fall back to heuristic."""
    decisions = Distiller(client=FakeClient(response="not json")).distill(
        trace_with_edits
    )
    # Heuristic fallback produces one low-confidence decision per edited file.
    assert len(decisions) == 1
    assert decisions[0].confidence == "low"
    assert "heuristic" in decisions[0].tags


def test_distill_strips_markdown_fences(trace_with_edits: SessionTrace) -> None:
    payload = (
        "```json\n"
        + json.dumps(
            [
                {
                    "chosen": "x",
                    "reasoning": "y",
                    "files": ["src/payment.ts"],
                }
            ]
        )
        + "\n```"
    )
    decisions = Distiller(client=FakeClient(response=payload)).distill(
        trace_with_edits
    )
    assert len(decisions) == 1


def test_distill_offline_falls_back_when_no_client(
    monkeypatch: pytest.MonkeyPatch,
    trace_with_edits: SessionTrace,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    decisions = Distiller().distill(trace_with_edits)
    assert len(decisions) == 1
    assert decisions[0].confidence == "low"
    assert "heuristic" in decisions[0].tags


def test_distill_falls_back_when_anthropic_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
    trace_with_edits: SessionTrace,
) -> None:
    """With an API key set but the optional SDK not installed, distiller
    must still produce a (heuristic) decision rather than crashing."""
    import builtins

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("RATIONALE_OFFLINE", raising=False)

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("simulated: anthropic not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    decisions = Distiller().distill(trace_with_edits)
    # Heuristic fallback kicked in, nothing raised
    assert len(decisions) == 1
    assert decisions[0].confidence == "low"
    assert "heuristic" in decisions[0].tags


def test_distill_resolves_fuzzy_anchor_paths() -> None:
    trace = SessionTrace(
        session_id="s",
        thinking=["x"],
        edits=[FileEdit("/abs/repo/src/payment.ts", 1, 5, "x")],
    )
    payload = json.dumps(
        [
            {
                "chosen": "x",
                "reasoning": "y",
                "files": ["src/payment.ts"],
            }
        ]
    )
    decisions = Distiller(client=FakeClient(response=payload)).distill(trace)
    assert decisions[0].anchors[0].file == "/abs/repo/src/payment.ts"


def test_distill_creates_anchor_for_file_without_edit() -> None:
    trace = SessionTrace(
        session_id="s",
        thinking=["x"],
        edits=[FileEdit("a.py", 1, 5, "x")],
    )
    payload = json.dumps(
        [
            {
                "chosen": "c",
                "reasoning": "r",
                "files": ["unrelated.py"],
            }
        ]
    )
    decisions = Distiller(client=FakeClient(response=payload)).distill(trace)
    assert len(decisions[0].anchors) == 1
    assert decisions[0].anchors[0].file == "unrelated.py"


def test_parse_json_array_handles_object_response() -> None:
    assert _parse_json_array('{"chosen": "x"}') == []


def test_parse_json_array_handles_empty_string() -> None:
    assert _parse_json_array("") == []


def test_decision_ids_are_stable_per_session_index_chosen() -> None:
    trace = SessionTrace(
        session_id="s",
        thinking=["t"],
        edits=[FileEdit("a.py", 1, 1, "x")],
    )
    payload = json.dumps([{"chosen": "x", "reasoning": "y", "files": ["a.py"]}])
    a = Distiller(client=FakeClient(response=payload)).distill(trace)
    b = Distiller(client=FakeClient(response=payload)).distill(trace)
    assert a[0].id == b[0].id

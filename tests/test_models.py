"""Tests for Decision and DecisionAnchor data models."""

from __future__ import annotations

import pytest

from rationale.models import Decision, DecisionAnchor


def test_anchor_contains_inclusive() -> None:
    a = DecisionAnchor(file="x.py", line_start=10, line_end=20)
    assert a.contains("x.py", 10)
    assert a.contains("x.py", 15)
    assert a.contains("x.py", 20)
    assert not a.contains("x.py", 9)
    assert not a.contains("x.py", 21)
    assert not a.contains("y.py", 15)


def test_anchor_roundtrip_dict() -> None:
    a = DecisionAnchor(file="x.py", line_start=1, line_end=5, ast_id="X.foo")
    data = a.to_dict()
    assert data == {"file": "x.py", "lines": [1, 5], "ast_id": "X.foo"}
    assert DecisionAnchor.from_dict(data) == a


def test_anchor_dict_without_ast() -> None:
    a = DecisionAnchor(file="x.py", line_start=1, line_end=5)
    assert "ast_id" not in a.to_dict()


def test_decision_requires_id_chosen_reasoning() -> None:
    with pytest.raises(ValueError):
        Decision(id="", timestamp="t", agent="a", chosen="c", reasoning="r")
    with pytest.raises(ValueError):
        Decision(id="d-1", timestamp="t", agent="a", chosen="", reasoning="r")
    with pytest.raises(ValueError):
        Decision(id="d-1", timestamp="t", agent="a", chosen="c", reasoning="")


def test_decision_rejects_bad_confidence() -> None:
    with pytest.raises(ValueError):
        Decision(
            id="d-1",
            timestamp="t",
            agent="a",
            chosen="c",
            reasoning="r",
            confidence="absolute",
        )


def test_decision_files_dedups_and_sorts() -> None:
    d = Decision(
        id="d-1",
        timestamp="t",
        agent="a",
        chosen="c",
        reasoning="r",
        anchors=[
            DecisionAnchor("z.py", 1, 2),
            DecisionAnchor("a.py", 3, 4),
            DecisionAnchor("a.py", 5, 6),
        ],
    )
    assert d.files == ["a.py", "z.py"]


def test_decision_frontmatter_roundtrip() -> None:
    original = Decision(
        id="d-abc123",
        timestamp="2026-04-16T12:00:00Z",
        agent="claude-code",
        chosen="fixed 3x retry",
        reasoning="Downstream rate limits already cap traffic.",
        anchors=[DecisionAnchor("src/payment.ts", 42, 58)],
        alternatives=["exponential_backoff", "circuit_breaker"],
        confidence="medium",
        tags=["reliability"],
        git_sha="deadbeef",
        session_id="sess-1",
    )
    fm = original.to_frontmatter()
    body = original.reasoning
    rebuilt = Decision.from_frontmatter(fm, body)
    assert rebuilt.id == original.id
    assert rebuilt.chosen == original.chosen
    assert rebuilt.anchors == original.anchors
    assert rebuilt.alternatives == original.alternatives
    assert rebuilt.tags == original.tags
    assert rebuilt.git_sha == original.git_sha

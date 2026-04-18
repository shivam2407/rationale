"""Tests for confidence-weighted rollups."""

from __future__ import annotations

from rationale.models import Decision, DecisionAnchor
from rationale.rollup import (
    Rollup,
    by_agent,
    by_file,
    by_tag,
    overall_summary,
)

# high=1.0, medium=0.6, low=0.25. The exact coefficients matter less than
# the ordering; tests only rely on the ordering being monotonic.


def _d(
    did: str,
    confidence: str = "medium",
    files: list[str] | None = None,
    tags: list[str] | None = None,
    agent: str = "claude-code",
) -> Decision:
    anchors = [
        DecisionAnchor(file=f, line_start=1, line_end=1)
        for f in (files or ["a.py"])
    ]
    return Decision(
        id=did,
        timestamp="2026-04-17T00:00:00Z",
        agent=agent,
        chosen="c",
        reasoning="r",
        anchors=anchors,
        confidence=confidence,
        tags=list(tags or []),
    )


def test_empty_store_produces_empty_rollups() -> None:
    assert by_file([]) == []
    assert by_agent([]) == []
    assert by_tag([]) == []


def test_by_file_counts_and_weights() -> None:
    decisions = [
        _d("d-1", "high", ["payment.py"]),
        _d("d-2", "low", ["payment.py"]),
        _d("d-3", "medium", ["cart.py"]),
    ]
    rollups = {r.key: r for r in by_file(decisions)}
    assert rollups["payment.py"].count == 2
    assert rollups["cart.py"].count == 1
    # payment has one high + one low → weight 1.25; cart has one medium → 0.6
    assert rollups["payment.py"].weight > rollups["cart.py"].weight


def test_by_agent_groups_by_agent_name() -> None:
    decisions = [
        _d("d-1", "medium", agent="claude-code"),
        _d("d-2", "high", agent="claude-code"),
        _d("d-3", "low", agent="cursor"),
    ]
    rollups = {r.key: r for r in by_agent(decisions)}
    assert rollups["claude-code"].count == 2
    assert rollups["cursor"].count == 1


def test_by_tag_counts_every_tag_once_per_decision() -> None:
    decisions = [
        _d("d-1", tags=["reliability", "payments"]),
        _d("d-2", tags=["reliability"]),
    ]
    rollups = {r.key: r for r in by_tag(decisions)}
    assert rollups["reliability"].count == 2
    assert rollups["payments"].count == 1


def test_overall_summary_reports_confidence_distribution() -> None:
    decisions = [
        _d("d-1", "high"),
        _d("d-2", "high"),
        _d("d-3", "medium"),
        _d("d-4", "low"),
    ]
    summary = overall_summary(decisions)
    assert summary.total == 4
    assert summary.by_confidence["high"] == 2
    assert summary.by_confidence["medium"] == 1
    assert summary.by_confidence["low"] == 1
    # Weighted score: 2*1.0 + 1*0.6 + 1*0.25 = 2.85
    assert round(summary.weighted_score, 2) == 2.85


def test_rollups_sorted_by_weight_descending() -> None:
    decisions = [
        _d("d-a", "low", ["a.py"]),
        _d("d-b", "high", ["b.py"]),
        _d("d-c", "medium", ["c.py"]),
    ]
    rollups = by_file(decisions)
    weights = [r.weight for r in rollups]
    assert weights == sorted(weights, reverse=True)


def test_rollup_includes_sample_ids() -> None:
    decisions = [
        _d("d-1", "high", ["x.py"]),
        _d("d-2", "high", ["x.py"]),
        _d("d-3", "high", ["x.py"]),
        _d("d-4", "high", ["x.py"]),
    ]
    r = by_file(decisions)[0]
    # Sample ids capped at the documented limit (default 3) so CLI output
    # doesn't explode on large stores.
    assert len(r.sample_ids) <= 3


def test_decision_without_anchors_counts_under_unknown_file_bucket() -> None:
    d = Decision(id="d-bare", timestamp="t", agent="a", chosen="c", reasoning="r")
    rollups = by_file([d])
    # A decision with no anchors should still be counted, bucketed under a
    # sentinel — otherwise rollup.total won't equal len(decisions).
    assert rollups
    assert sum(r.count for r in rollups) == 1


def test_rollup_dataclass_is_frozen() -> None:
    r = Rollup(key="x", count=1, weight=1.0, sample_ids=("d-1",))
    # frozen dataclasses raise FrozenInstanceError; we just assert immutability
    import dataclasses

    try:
        r.count = 2  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Rollup must be frozen")

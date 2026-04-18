"""Tests for the query layer."""

from __future__ import annotations

from pathlib import Path

from rationale.models import Decision, DecisionAnchor
from rationale.query import query
from rationale.storage import DecisionStore


def _seed(store: DecisionStore) -> None:
    store.save(
        Decision(
            id="d-pay001",
            timestamp="2026-04-16T12:00:00Z",
            agent="claude-code",
            chosen="fixed 3x retry",
            reasoning="Downstream rate limits already cap traffic.",
            anchors=[DecisionAnchor("src/payment.ts", 42, 58)],
            alternatives=["exponential_backoff"],
            confidence="medium",
            tags=["reliability"],
        )
    )
    store.save(
        Decision(
            id="d-cac002",
            timestamp="2026-04-15T12:00:00Z",
            agent="claude-code",
            chosen="LRU cache",
            reasoning="Bounded memory matters more than hit rate.",
            anchors=[DecisionAnchor("src/cache.ts", 10, 30)],
            tags=["performance"],
        )
    )


def test_query_by_exact_line(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "src/payment.ts:42")
    assert [h.id for h in hits] == ["d-pay001"]
    assert hits[0].reason == "exact-line"


def test_query_by_near_line(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "src/payment.ts:80")
    assert [h.id for h in hits] == ["d-pay001"]
    assert hits[0].reason == "near-line"


def test_query_by_far_line_returns_nothing(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "src/payment.ts:500")
    assert hits == []


def test_query_by_file(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "src/cache.ts")
    assert [h.id for h in hits] == ["d-cac002"]


def test_query_by_text(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "retry")
    assert "d-pay001" in [h.id for h in hits]


def test_query_text_matches_chosen_with_higher_score(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    _seed(store)
    hits = query(store, "LRU")
    assert hits[0].id == "d-cac002"
    assert hits[0].score == 1.0


def test_query_returns_empty_when_no_decisions(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    assert query(store, "anything") == []

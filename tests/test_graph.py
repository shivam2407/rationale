"""Tests for the decision graph — related/superseded/contradicting edges."""

from __future__ import annotations

from rationale.graph import (
    DecisionGraph,
    EdgeKind,
    build_graph,
    neighbors_of,
)
from rationale.models import Decision, DecisionAnchor


def _d(
    did: str,
    chosen: str,
    file: str,
    line_start: int,
    line_end: int,
    *,
    timestamp: str = "2026-04-17T00:00:00Z",
    symbol: str | None = None,
    tags: list[str] | None = None,
) -> Decision:
    return Decision(
        id=did,
        timestamp=timestamp,
        agent="test",
        chosen=chosen,
        reasoning="r",
        anchors=[
            DecisionAnchor(
                file=file,
                line_start=line_start,
                line_end=line_end,
                symbol=symbol,
            )
        ],
        tags=tags or [],
    )


def test_empty_store_yields_empty_graph() -> None:
    g = build_graph([])
    assert isinstance(g, DecisionGraph)
    assert g.nodes == []
    assert g.edges == []


def test_single_decision_has_no_edges() -> None:
    g = build_graph([_d("d-1", "retry 3x", "a.py", 1, 5)])
    assert len(g.nodes) == 1
    assert g.edges == []


def test_same_symbol_different_choice_creates_supersession_edge() -> None:
    """Two decisions about PaymentService.retry where the later one picks
    a different option. Graph should record SUPERSEDES from later to earlier."""
    older = _d(
        "d-old",
        "retry 3x",
        "src/payment.py",
        1,
        10,
        timestamp="2026-01-01T00:00:00Z",
        symbol="PaymentService.retry",
    )
    newer = _d(
        "d-new",
        "exponential backoff",
        "src/payment.py",
        1,
        10,
        timestamp="2026-04-01T00:00:00Z",
        symbol="PaymentService.retry",
    )
    g = build_graph([older, newer])

    sup = [e for e in g.edges if e.kind == EdgeKind.SUPERSEDES]
    assert len(sup) == 1
    assert sup[0].source == "d-new"
    assert sup[0].target == "d-old"


def test_same_symbol_same_choice_is_related_not_supersession() -> None:
    """If two decisions agree on the same symbol, they're RELATED, not
    supersessions — one reinforces the other."""
    first = _d(
        "d-1",
        "retry 3x",
        "src/payment.py",
        1,
        10,
        timestamp="2026-01-01T00:00:00Z",
        symbol="PaymentService.retry",
    )
    second = _d(
        "d-2",
        "retry 3x",
        "src/payment.py",
        1,
        10,
        timestamp="2026-04-01T00:00:00Z",
        symbol="PaymentService.retry",
    )
    g = build_graph([first, second])
    kinds = {e.kind for e in g.edges}
    assert EdgeKind.SUPERSEDES not in kinds
    assert EdgeKind.RELATED in kinds


def test_overlapping_line_ranges_on_same_file_link_related() -> None:
    """Decisions about overlapping line ranges are RELATED even when the
    symbol metadata is missing (v0 anchors)."""
    a = _d("d-a", "choice A", "src/x.py", 10, 20)
    b = _d("d-b", "choice B", "src/x.py", 18, 30)
    g = build_graph([a, b])
    related = [e for e in g.edges if e.kind == EdgeKind.RELATED]
    assert related


def test_non_overlapping_different_files_no_edge() -> None:
    a = _d("d-a", "x", "src/x.py", 1, 10)
    b = _d("d-b", "y", "src/y.py", 1, 10)
    g = build_graph([a, b])
    assert g.edges == []


def test_neighbors_of_returns_both_directions() -> None:
    older = _d(
        "d-old",
        "A",
        "src/x.py",
        1,
        10,
        timestamp="2026-01-01T00:00:00Z",
        symbol="foo",
    )
    newer = _d(
        "d-new",
        "B",
        "src/x.py",
        1,
        10,
        timestamp="2026-04-01T00:00:00Z",
        symbol="foo",
    )
    g = build_graph([older, newer])

    ids = {n.id for n in neighbors_of(g, "d-new")}
    assert "d-old" in ids
    ids = {n.id for n in neighbors_of(g, "d-old")}
    assert "d-new" in ids


def test_supersession_prefers_exact_chosen_text_comparison() -> None:
    """Chosen strings are compared case-insensitively, whitespace-stripped,
    so trivial formatting differences don't fire false SUPERSEDES edges."""
    a = _d(
        "d-1",
        "Retry 3x ",
        "x.py",
        1,
        5,
        timestamp="2026-01-01T00:00:00Z",
        symbol="f",
    )
    b = _d(
        "d-2",
        "retry 3x",
        "x.py",
        1,
        5,
        timestamp="2026-04-01T00:00:00Z",
        symbol="f",
    )
    g = build_graph([a, b])
    kinds = {e.kind for e in g.edges}
    assert EdgeKind.SUPERSEDES not in kinds


def test_supersession_direction_is_always_newer_to_older() -> None:
    """Even if the decisions are passed in chronological or reverse order,
    the SUPERSEDES edge points from newer → older."""
    older = _d(
        "d-old",
        "A",
        "x.py",
        1,
        5,
        timestamp="2026-01-01T00:00:00Z",
        symbol="f",
    )
    newer = _d(
        "d-new",
        "B",
        "x.py",
        1,
        5,
        timestamp="2026-04-01T00:00:00Z",
        symbol="f",
    )
    g_rev = build_graph([newer, older])  # reversed input
    sup = [e for e in g_rev.edges if e.kind == EdgeKind.SUPERSEDES]
    assert len(sup) == 1
    assert sup[0].source == "d-new"
    assert sup[0].target == "d-old"


def test_decisions_without_anchors_do_not_crash() -> None:
    d = Decision(
        id="d-bare", timestamp="t", agent="a", chosen="c", reasoning="r"
    )
    g = build_graph([d])
    assert len(g.nodes) == 1
    assert g.edges == []


def test_graph_edge_is_symmetric_for_related_but_not_supersedes() -> None:
    """RELATED is undirected (we emit one edge, not two, but querying by
    either endpoint finds it). SUPERSEDES is directed."""
    a = _d("d-a", "choice A", "src/x.py", 10, 20)
    b = _d("d-b", "choice B", "src/x.py", 18, 30)
    g = build_graph([a, b])
    a_neighbors = {n.id for n in neighbors_of(g, "d-a")}
    b_neighbors = {n.id for n in neighbors_of(g, "d-b")}
    assert "d-b" in a_neighbors and "d-a" in b_neighbors

"""Decision graph: relate, supersede, and compare decisions.

A single decision is a snapshot. A graph of decisions is how you see
*history* — which calls were walked back, which calls reinforced each
other, and which areas of the code attract the most deliberation.

v2 scope keeps this deliberately simple:

- Two decisions are RELATED when they touch the same file with overlapping
  line ranges, or the same symbol path.
- A newer decision SUPERSEDES an older one when they target the same
  symbol AND pick a different option. This is the closest thing to
  "this decision was walked back" that we can compute without semantic
  search.

There is no fuzzy alternatives-overlap or embedding-based contradiction
detection yet — that lives behind a plug-in interface in a later release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rationale.models import Decision, DecisionAnchor


class EdgeKind(str, Enum):
    RELATED = "related"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True)
class Edge:
    source: str  # decision id
    target: str
    kind: EdgeKind
    reason: str = ""


@dataclass
class DecisionGraph:
    nodes: list[Decision] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def node_by_id(self, did: str) -> Decision | None:
        for n in self.nodes:
            if n.id == did:
                return n
        return None


def build_graph(decisions: list[Decision]) -> DecisionGraph:
    """Build a relationship graph over the supplied decisions."""
    graph = DecisionGraph(nodes=list(decisions))
    if len(decisions) < 2:
        return graph

    seen: set[tuple[str, str, EdgeKind]] = set()
    for i, a in enumerate(decisions):
        for b in decisions[i + 1 :]:
            for edge in _edges_between(a, b):
                key = (edge.source, edge.target, edge.kind)
                if key not in seen:
                    seen.add(key)
                    graph.edges.append(edge)
    return graph


def neighbors_of(graph: DecisionGraph, decision_id: str) -> list[Decision]:
    """All decisions connected to `decision_id` by any edge, in either direction."""
    ids: set[str] = set()
    for e in graph.edges:
        if e.source == decision_id:
            ids.add(e.target)
        elif e.target == decision_id:
            ids.add(e.source)
    return [n for n in graph.nodes if n.id in ids]


# --- Internal helpers -------------------------------------------------------


def _edges_between(a: Decision, b: Decision) -> list[Edge]:
    edges: list[Edge] = []
    older, newer = _chronological(a, b)
    share_symbol, symbol = _shared_symbol(a, b)

    if share_symbol and _same_choice(older, newer):
        edges.append(
            Edge(
                source=newer.id,
                target=older.id,
                kind=EdgeKind.RELATED,
                reason=f"both anchor symbol {symbol}",
            )
        )
        return edges

    if share_symbol and not _same_choice(older, newer):
        edges.append(
            Edge(
                source=newer.id,
                target=older.id,
                kind=EdgeKind.SUPERSEDES,
                reason=f"later decision on symbol {symbol} picked a different option",
            )
        )
        return edges

    if _overlapping_file_ranges(a, b):
        edges.append(
            Edge(
                source=newer.id,
                target=older.id,
                kind=EdgeKind.RELATED,
                reason="overlapping line ranges in the same file",
            )
        )

    return edges


def _chronological(a: Decision, b: Decision) -> tuple[Decision, Decision]:
    """Return (older, newer) by timestamp; break ties on id for determinism."""
    if (a.timestamp, a.id) <= (b.timestamp, b.id):
        return a, b
    return b, a


def _shared_symbol(a: Decision, b: Decision) -> tuple[bool, str | None]:
    a_symbols = {x.symbol for x in a.anchors if x.symbol}
    b_symbols = {x.symbol for x in b.anchors if x.symbol}
    common = a_symbols & b_symbols
    if common:
        return True, sorted(common)[0]
    return False, None


def _same_choice(a: Decision, b: Decision) -> bool:
    return _normalize_choice(a.chosen) == _normalize_choice(b.chosen)


def _normalize_choice(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _overlapping_file_ranges(a: Decision, b: Decision) -> bool:
    for aa in a.anchors:
        for ba in b.anchors:
            if not _same_file(aa, ba):
                continue
            if aa.line_start <= ba.line_end and ba.line_start <= aa.line_end:
                return True
    return False


def _same_file(a: DecisionAnchor, b: DecisionAnchor) -> bool:
    # Use the same relaxed equality as the anchoring layer so that a
    # repo-relative path matches its absolute counterpart.
    from rationale.anchoring import _file_eq

    return _file_eq(a.file, b.file)

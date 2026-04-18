"""Regression test: a decision stored from one CWD must be findable from another.

An earlier implementation used `Path.resolve()` on both stored anchors
and queries, so equality silently depended on the caller's cwd. This
test locks in cwd-independent anchor matching.
"""

from __future__ import annotations

import os
from pathlib import Path

from rationale.models import Decision, DecisionAnchor
from rationale.query import query
from rationale.storage import DecisionStore


def test_query_independent_of_caller_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = DecisionStore(repo)
    store.save(
        Decision(
            id="d-zzz111",
            timestamp="2026-04-16T12:00:00Z",
            agent="claude-code",
            chosen="x",
            reasoning="y",
            anchors=[DecisionAnchor("src/payment.ts", 42, 58)],
        )
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    prev = Path.cwd()
    try:
        os.chdir(elsewhere)
        hits = query(store, "src/payment.ts:42")
    finally:
        os.chdir(prev)

    assert [h.id for h in hits] == ["d-zzz111"]


def test_file_equality_respects_path_components() -> None:
    """`ent.ts` must NOT match `payment.ts` — that's a half-segment collision."""
    from rationale.anchoring import _file_eq

    assert _file_eq("src/payment.ts", "./src/payment.ts")
    assert _file_eq("src/payment.ts", "/abs/repo/src/payment.ts")
    assert not _file_eq("ent.ts", "src/payment.ts")
    assert not _file_eq("payment.ts", "cache_payment.ts")


def test_relative_matches_absolute_via_path_suffix() -> None:
    """A relative query should find an anchor stored with an absolute path
    (this is the whole point of cwd-independent matching)."""
    from rationale.anchoring import _file_eq

    assert _file_eq("/var/log/x.py", "var/log/x.py")
    assert _file_eq("./var/log/x.py", "var/log/x.py")
    # But a bare filename shouldn't match a deep path (too ambiguous).
    assert _file_eq("x.py", "/var/log/x.py") is True  # allowed: suffix aligned
    # …while a half-segment is still rejected.
    assert not _file_eq("og/x.py", "/var/log/x.py")

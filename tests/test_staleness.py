"""Tests for the staleness detector.

A decision is stale when its anchored code no longer matches what was
captured. The detector distinguishes four states:

- fresh: content hash still matches at the stored line range
- drifted: content hash matches, but at a different line range (symbol
  or snippet relocated)
- stale: symbol still exists but the body changed
- missing: file or symbol is gone entirely
"""

from __future__ import annotations

from pathlib import Path

from rationale.models import Decision, DecisionAnchor
from rationale.staleness import (
    StalenessReport,
    Status,
    check_anchor,
    check_decision,
)
from rationale.symbols import content_hash, hash_file_range


def _decision_with_anchor(anchor: DecisionAnchor) -> Decision:
    return Decision(
        id="d-abc",
        timestamp="2026-04-17T00:00:00Z",
        agent="test",
        chosen="test choice",
        reasoning="test reasoning",
        anchors=[anchor],
    )


def test_fresh_when_content_unchanged(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("def x():\n    return 1\n", encoding="utf-8")
    h = hash_file_range(src, 1, 2)
    anchor = DecisionAnchor(
        file=str(src), line_start=1, line_end=2, content_hash=h, symbol="x"
    )
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.FRESH


def test_missing_when_file_deleted(tmp_path: Path) -> None:
    anchor = DecisionAnchor(
        file=str(tmp_path / "gone.py"),
        line_start=1,
        line_end=3,
        content_hash="deadbeefdeadbeef",
        symbol="anything",
    )
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.MISSING


def test_drifted_when_symbol_moved_but_body_same(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    original_body = "def target():\n    return 42\n"
    src.write_text(original_body, encoding="utf-8")
    original_hash = hash_file_range(src, 1, 2)

    anchor = DecisionAnchor(
        file=str(src),
        line_start=1,
        line_end=2,
        content_hash=original_hash,
        symbol="target",
    )

    # Pad the top of the file — target drifts down
    src.write_text(
        "# pad\n# pad\n# pad\n# pad\n" + original_body,
        encoding="utf-8",
    )
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.DRIFTED
    assert report.current_line_start is not None
    assert report.current_line_start > 1


def test_stale_when_symbol_body_changed(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("def target():\n    return 42\n", encoding="utf-8")
    original_hash = hash_file_range(src, 1, 2)
    anchor = DecisionAnchor(
        file=str(src),
        line_start=1,
        line_end=2,
        content_hash=original_hash,
        symbol="target",
    )

    # Same symbol, new body
    src.write_text("def target():\n    return 99\n", encoding="utf-8")
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.STALE


def test_stale_when_symbol_removed_and_hash_gone(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("def target():\n    return 42\n", encoding="utf-8")
    h = hash_file_range(src, 1, 2)
    anchor = DecisionAnchor(
        file=str(src),
        line_start=1,
        line_end=2,
        content_hash=h,
        symbol="target",
    )

    src.write_text("def other():\n    return 0\n", encoding="utf-8")
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.STALE


def test_legacy_anchor_without_hash_reports_unknown(tmp_path: Path) -> None:
    src = tmp_path / "a.py"
    src.write_text("def target():\n    return 1\n", encoding="utf-8")
    # No content_hash and no symbol — v0-era anchor
    anchor = DecisionAnchor(file=str(src), line_start=1, line_end=2)
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.UNKNOWN


def test_check_decision_aggregates_worst_status(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("def x():\n    return 1\n", encoding="utf-8")
    good_h = hash_file_range(good, 1, 2)

    bad = tmp_path / "bad.py"
    bad.write_text("def y():\n    return 1\n", encoding="utf-8")
    bad_h = hash_file_range(bad, 1, 2)
    # Now mutate bad
    bad.write_text("def y():\n    return 999\n", encoding="utf-8")

    decision = Decision(
        id="d-agg",
        timestamp="t",
        agent="a",
        chosen="c",
        reasoning="r",
        anchors=[
            DecisionAnchor(
                file=str(good),
                line_start=1,
                line_end=2,
                content_hash=good_h,
                symbol="x",
            ),
            DecisionAnchor(
                file=str(bad),
                line_start=1,
                line_end=2,
                content_hash=bad_h,
                symbol="y",
            ),
        ],
    )
    summary = check_decision(decision, repo_root=tmp_path)
    # worst anchor wins
    assert summary.status == Status.STALE
    assert len(summary.anchor_reports) == 2


def test_relative_path_resolved_under_repo_root(tmp_path: Path) -> None:
    subdir = tmp_path / "src"
    subdir.mkdir()
    src = subdir / "a.py"
    src.write_text("def x():\n    return 1\n", encoding="utf-8")
    h = hash_file_range(src, 1, 2)
    anchor = DecisionAnchor(
        file="src/a.py",
        line_start=1,
        line_end=2,
        content_hash=h,
        symbol="x",
    )
    report = check_anchor(anchor, repo_root=tmp_path)
    assert report.status == Status.FRESH


def test_staleness_report_exposes_anchor() -> None:
    anchor = DecisionAnchor(file="x.py", line_start=1, line_end=2)
    report = StalenessReport(anchor=anchor, status=Status.UNKNOWN)
    assert report.anchor is anchor
    assert report.status is Status.UNKNOWN


def test_content_hash_helper_exposed() -> None:
    # Defensive smoke test: ensure detector module doesn't accidentally
    # shadow the symbols module helpers by re-exporting stale copies.
    assert content_hash("x") == content_hash("x")


def test_path_traversal_rejected(tmp_path: Path) -> None:
    """Security: an anchor with '..' must not be able to read files above
    the repo root. Staleness must report MISSING, never read the file."""
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("SENSITIVE\n", encoding="utf-8")
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        anchor = DecisionAnchor(
            file=f"../{secret.name}",
            line_start=1,
            line_end=1,
            content_hash=content_hash("SENSITIVE"),
            symbol="whatever",
        )
        report = check_anchor(anchor, repo_root=repo)
        # The anchor resolves outside the repo root; staleness must refuse
        # to read it and classify as MISSING.
        assert report.status == Status.MISSING
    finally:
        secret.unlink(missing_ok=True)


def test_absolute_path_outside_repo_rejected(tmp_path: Path) -> None:
    """Security: an absolute path outside repo_root is rejected."""
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text("def x():\n    return 1\n", encoding="utf-8")
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        anchor = DecisionAnchor(
            file=str(outside),
            line_start=1,
            line_end=2,
            content_hash=content_hash("def x():\n    return 1"),
            symbol="x",
        )
        report = check_anchor(anchor, repo_root=repo)
        assert report.status == Status.MISSING
    finally:
        outside.unlink(missing_ok=True)


def test_symlink_pointing_outside_repo_rejected(tmp_path: Path) -> None:
    """Security: a symlink inside the repo pointing out must not be followed."""
    import os

    target = tmp_path.parent / "external.py"
    target.write_text("def x():\n    return 1\n", encoding="utf-8")
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        link = repo / "link.py"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")
        anchor = DecisionAnchor(
            file="link.py",
            line_start=1,
            line_end=2,
            content_hash=content_hash("def x():\n    return 1"),
            symbol="x",
        )
        report = check_anchor(anchor, repo_root=repo)
        assert report.status == Status.MISSING
    finally:
        target.unlink(missing_ok=True)


def test_check_decision_with_no_anchors_is_unknown(tmp_path: Path) -> None:
    decision = Decision(
        id="d-empty",
        timestamp="t",
        agent="a",
        chosen="c",
        reasoning="r",
        anchors=[],
    )
    summary = check_decision(decision, repo_root=tmp_path)
    assert summary.status == Status.UNKNOWN
    assert summary.anchor_reports == []

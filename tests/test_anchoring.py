"""Tests for the anchoring layer (line-range matching with drift tolerance)."""

from __future__ import annotations

from rationale.anchoring import DRIFT_TOLERANCE, matches_file, matches_line
from rationale.models import Decision, DecisionAnchor


def _decision(file: str, line_start: int, line_end: int) -> Decision:
    return Decision(
        id="d-x",
        timestamp="t",
        agent="a",
        chosen="c",
        reasoning="r",
        anchors=[DecisionAnchor(file, line_start, line_end)],
    )


def test_exact_line_match() -> None:
    d = _decision("src/x.py", 10, 20)
    assert matches_line(d, "src/x.py", 15)


def test_boundary_line_match() -> None:
    d = _decision("src/x.py", 10, 20)
    assert matches_line(d, "src/x.py", 10)
    assert matches_line(d, "src/x.py", 20)


def test_drift_tolerance_match() -> None:
    d = _decision("src/x.py", 100, 110)
    assert matches_line(d, "src/x.py", 100 - DRIFT_TOLERANCE)
    assert matches_line(d, "src/x.py", 110 + DRIFT_TOLERANCE)


def test_far_line_does_not_match() -> None:
    d = _decision("src/x.py", 100, 110)
    assert not matches_line(d, "src/x.py", 100 - DRIFT_TOLERANCE - 5)
    assert not matches_line(d, "src/x.py", 110 + DRIFT_TOLERANCE + 5)


def test_different_file_does_not_match() -> None:
    d = _decision("src/x.py", 1, 100)
    assert not matches_line(d, "src/y.py", 50)


def test_matches_file_true_for_anchored_file() -> None:
    d = _decision("src/x.py", 1, 5)
    assert matches_file(d, "src/x.py")


def test_matches_file_false_for_other_file() -> None:
    d = _decision("src/x.py", 1, 5)
    assert not matches_file(d, "src/y.py")


def test_normalize_handles_relative_and_dot_prefix() -> None:
    d = _decision("./src/x.py", 1, 5)
    assert matches_file(d, "src/x.py")

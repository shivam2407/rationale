"""Confidence-weighted rollups for tech leads.

Individual decisions are too granular to scan at scale. A rollup answers
"where is deliberation concentrated?" across files, agents, and tags.
Each decision contributes its confidence as a weight: a high-confidence
decision counts more toward the signal than a low-confidence hedge.

Weights:
- high   → 1.00
- medium → 0.60
- low    → 0.25

These coefficients are deliberately round numbers. The absolute values
don't matter; only the ordering does. Tech leads should use rollups to
spot concentration, not to draw statistical conclusions from the numbers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from rationale.models import Decision


CONFIDENCE_WEIGHTS: dict[str, float] = {
    "high": 1.00,
    "medium": 0.60,
    "low": 0.25,
}

_UNKNOWN_FILE = "(no anchors)"
_MAX_SAMPLE_IDS = 3


@dataclass(frozen=True)
class Rollup:
    """One bucket of a grouped summary."""

    key: str
    count: int
    weight: float
    sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverallSummary:
    """Store-level summary."""

    total: int
    by_confidence: dict[str, int]
    weighted_score: float


def by_file(decisions: Iterable[Decision]) -> list[Rollup]:
    grouped: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        if not d.anchors:
            grouped[_UNKNOWN_FILE].append(d)
            continue
        # Count each distinct file once per decision
        seen: set[str] = set()
        for a in d.anchors:
            if a.file in seen:
                continue
            seen.add(a.file)
            grouped[a.file].append(d)
    return _finalize(grouped)


def by_agent(decisions: Iterable[Decision]) -> list[Rollup]:
    grouped: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        grouped[d.agent or "unknown"].append(d)
    return _finalize(grouped)


def by_tag(decisions: Iterable[Decision]) -> list[Rollup]:
    grouped: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        for t in set(d.tags):
            grouped[t].append(d)
    return _finalize(grouped)


def overall_summary(decisions: Iterable[Decision]) -> OverallSummary:
    confidence_counts: Counter[str] = Counter()
    total = 0
    weighted = 0.0
    for d in decisions:
        total += 1
        confidence_counts[d.confidence] += 1
        weighted += CONFIDENCE_WEIGHTS.get(d.confidence, 0.0)
    return OverallSummary(
        total=total,
        by_confidence=dict(confidence_counts),
        weighted_score=weighted,
    )


# --- Internal helpers -------------------------------------------------------


def _finalize(groups: dict[str, list[Decision]]) -> list[Rollup]:
    rollups = [
        Rollup(
            key=key,
            count=len(items),
            weight=sum(CONFIDENCE_WEIGHTS.get(i.confidence, 0.0) for i in items),
            sample_ids=tuple(i.id for i in items[:_MAX_SAMPLE_IDS]),
        )
        for key, items in groups.items()
    ]
    rollups.sort(key=lambda r: (-r.weight, -r.count, r.key))
    return rollups

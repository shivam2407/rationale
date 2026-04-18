"""Staleness detection: does the anchored code still match what was captured?

A decision is only as useful as the link to its code. When the code
underneath drifts or changes, the recorded reasoning may no longer apply.
This module compares the anchor's stored symbol + content_hash against
the current working tree and classifies the outcome.

Four statuses:

- FRESH    — content hash still matches at the stored line range
- DRIFTED  — content hash matches, but the block moved to a new line range
             (symbol relocated after a refactor above it). Still valid.
- STALE    — symbol still exists or lines exist, but the body changed
- MISSING  — file is gone, unreadable, or symbol/lines no longer resolve
- UNKNOWN  — no content_hash stored (legacy v0 anchor); we can't decide
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rationale.models import Decision, DecisionAnchor
from rationale.symbols import find_symbol, hash_file_range


class Status(str, Enum):
    FRESH = "fresh"
    DRIFTED = "drifted"
    STALE = "stale"
    MISSING = "missing"
    UNKNOWN = "unknown"


# Severity ranking used when a decision has multiple anchors and we need
# a single rollup status. Worst status wins. MISSING outranks STALE so
# an operator sees "file is gone" before "body changed".
_SEVERITY: dict[Status, int] = {
    Status.FRESH: 0,
    Status.UNKNOWN: 1,
    Status.DRIFTED: 2,
    Status.STALE: 3,
    Status.MISSING: 4,
}


@dataclass(frozen=True)
class StalenessReport:
    """Per-anchor verdict."""

    anchor: DecisionAnchor
    status: Status
    current_line_start: int | None = None
    current_line_end: int | None = None
    detail: str = ""


@dataclass
class DecisionStaleness:
    """Aggregated verdict for a whole decision (worst-anchor-wins)."""

    decision: Decision
    status: Status
    anchor_reports: list[StalenessReport] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return self.status in {Status.STALE, Status.MISSING}


def check_anchor(
    anchor: DecisionAnchor, repo_root: Path | str | None = None
) -> StalenessReport:
    """Classify a single anchor against the current working tree."""
    path = _resolve_path(anchor.file, repo_root)
    if path is None or not path.exists():
        return StalenessReport(
            anchor=anchor,
            status=Status.MISSING,
            detail=f"file not found: {anchor.file}",
        )

    if not anchor.content_hash:
        # v0 anchors had no fingerprint — we can't tell fresh from stale
        return StalenessReport(
            anchor=anchor,
            status=Status.UNKNOWN,
            detail="anchor has no content_hash (legacy v0)",
        )

    current_hash_at_stored_range = hash_file_range(
        path, anchor.line_start, anchor.line_end
    )
    if current_hash_at_stored_range == anchor.content_hash:
        return StalenessReport(
            anchor=anchor,
            status=Status.FRESH,
            current_line_start=anchor.line_start,
            current_line_end=anchor.line_end,
        )

    # Try to re-locate via the symbol, which survives line drift.
    if anchor.symbol:
        sym = find_symbol(path, anchor.symbol)
        if sym is not None:
            sym_hash = hash_file_range(path, sym.line_start, sym.line_end)
            if sym_hash == anchor.content_hash:
                return StalenessReport(
                    anchor=anchor,
                    status=Status.DRIFTED,
                    current_line_start=sym.line_start,
                    current_line_end=sym.line_end,
                    detail=f"symbol '{anchor.symbol}' moved",
                )
            return StalenessReport(
                anchor=anchor,
                status=Status.STALE,
                current_line_start=sym.line_start,
                current_line_end=sym.line_end,
                detail=f"symbol '{anchor.symbol}' body changed",
            )

    # Symbol missing or not recorded — code at those lines has changed.
    return StalenessReport(
        anchor=anchor,
        status=Status.STALE,
        detail="content at anchor no longer matches",
    )


def check_decision(
    decision: Decision, repo_root: Path | str | None = None
) -> DecisionStaleness:
    reports = [check_anchor(a, repo_root=repo_root) for a in decision.anchors]
    if not reports:
        return DecisionStaleness(
            decision=decision, status=Status.UNKNOWN, anchor_reports=[]
        )
    worst = max(reports, key=lambda r: _SEVERITY[r.status])
    return DecisionStaleness(
        decision=decision, status=worst.status, anchor_reports=reports
    )


def _resolve_path(file: str, repo_root: Path | str | None) -> Path | None:
    """Resolve an anchor path, rejecting anything outside ``repo_root``.

    Anchors may be stored as absolute paths, repo-relative paths, or with
    leading ``./``. When a ``repo_root`` is supplied we fully resolve the
    candidate (following symlinks) and confirm it stays *inside* the repo.
    Paths that escape the root via ``..``, absolute paths pointing
    elsewhere, or symlinks aimed outside the repo all return None.

    This is the security boundary of the staleness detector: no file
    outside ``repo_root`` is ever read or hashed. Without this check, a
    malicious or stale anchor could coerce ``rationale check`` into
    reading arbitrary files.
    """
    if not file:
        return None
    candidate = Path(file)
    if repo_root is None:
        # Best-effort mode: used by tests that don't supply a root.
        return candidate

    root = Path(repo_root)
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        # Candidate is outside the repo root — reject.
        return None
    return resolved_candidate

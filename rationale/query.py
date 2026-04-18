"""Query layer: `why <file>:<line>`, `why "term"`, `why-list`."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rationale.anchoring import _file_eq, matches_file, matches_line
from rationale.models import Decision
from rationale.storage import DecisionStore

# Match `<file>:<line>` where file may itself contain a drive-letter colon
# on Windows (e.g. C:/src/x.py:42). We greedy-match the file and pin line
# to the trailing digits.
LINE_REF = re.compile(r"^(?P<file>.+):(?P<line>\d+)$")


@dataclass
class QueryHit:
    decision: Decision
    score: float
    reason: str

    @property
    def id(self) -> str:
        return self.decision.id


def query(store: DecisionStore, term: str) -> list[QueryHit]:
    """Dispatch on whether `term` looks like file:line, file, or free text."""
    decisions = store.all()
    if not decisions:
        return []

    line_match = LINE_REF.match(term.strip())
    if line_match:
        file = line_match.group("file")
        line = int(line_match.group("line"))
        return _by_line(decisions, file, line)

    if "/" in term or _looks_like_filename(term):
        return _by_file(decisions, term)

    return _by_text(decisions, term)


def _by_line(decisions: list[Decision], file: str, line: int) -> list[QueryHit]:
    hits: list[QueryHit] = []
    for d in decisions:
        if matches_line(d, file, line):
            exact = any(
                a.line_start <= line <= a.line_end
                for a in d.anchors
                if _file_eq(a.file, file)
            )
            hits.append(
                QueryHit(
                    decision=d,
                    score=1.0 if exact else 0.5,
                    reason="exact-line" if exact else "near-line",
                )
            )
    # Highest score first; newest first on ties.
    hits.sort(key=lambda h: (-h.score, _neg_ts(h.decision.timestamp)))
    return hits


def _by_file(decisions: list[Decision], file: str) -> list[QueryHit]:
    hits = [
        QueryHit(decision=d, score=0.8, reason="file-match")
        for d in decisions
        if matches_file(d, file)
    ]
    hits.sort(key=lambda h: h.decision.timestamp, reverse=True)
    return hits


def _by_text(decisions: list[Decision], term: str) -> list[QueryHit]:
    needle = term.lower().strip()
    if not needle:
        return []
    hits: list[QueryHit] = []
    for d in decisions:
        haystack = " ".join(
            [d.chosen, d.reasoning, " ".join(d.tags), " ".join(d.alternatives)]
        ).lower()
        if needle in haystack:
            score = 1.0 if needle in d.chosen.lower() else 0.7
            hits.append(QueryHit(decision=d, score=score, reason="text-match"))
    hits.sort(key=lambda h: (-h.score, _neg_ts(h.decision.timestamp)))
    return hits


_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\-]+\.[A-Za-z0-9]{1,6}$")


def _looks_like_filename(term: str) -> bool:
    """True for any `name.ext` where ext is 1-6 alphanumeric chars."""
    return bool(_FILENAME_RE.match(term))


def _neg_ts(ts: str) -> str:
    """Invert a lexically-sortable ISO timestamp so 'newer first' works as a sort key."""
    # Flip each digit so 2026 > 2025 becomes "inverted-2026" < "inverted-2025".
    flipped_digits = str.maketrans("0123456789", "9876543210")
    return ts.translate(flipped_digits)

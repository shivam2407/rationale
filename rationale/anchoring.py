"""Anchoring layer: keep decisions linked to code as it moves.

v0 stores absolute or repo-relative line ranges. When we look up
`why <file>:<line>`, we fall back to a *fuzzy* match if the exact range
no longer applies — for example, when lines have been inserted or
removed above the anchor.

Tree-sitter / AST anchors arrive in v1.
"""

from __future__ import annotations

from rationale.models import Decision

# How far we'll drift before refusing to call a decision "still relevant"
# for a given line. Tuned so a moderate refactor (a function moving down
# 30 lines) still surfaces the rationale.
DRIFT_TOLERANCE = 40


def matches_line(decision: Decision, file: str, line: int) -> bool:
    if not _files_match(file, [a.file for a in decision.anchors]):
        return False
    for anchor in decision.anchors:
        if not _file_eq(anchor.file, file):
            continue
        if anchor.line_start <= line <= anchor.line_end:
            return True
        if abs(anchor.line_start - line) <= DRIFT_TOLERANCE:
            return True
        if abs(anchor.line_end - line) <= DRIFT_TOLERANCE:
            return True
    return False


def matches_file(decision: Decision, file: str) -> bool:
    return any(_file_eq(a.file, file) for a in decision.anchors)


def _file_eq(a: str, b: str) -> bool:
    """Path equality that respects directory components.

    - 'src/x.py' == './src/x.py'
    - 'src/x.py' == '/repo/src/x.py' if 'src/x.py' is a path-component suffix
    - 'ent.ts' != 'src/payment.ts'  (no half-segment matches)
    """
    na = _normalize(a)
    nb = _normalize(b)
    if na == nb:
        return True
    return _suffix_match(na, nb) or _suffix_match(nb, na)


def _files_match(needle: str, haystack: list[str]) -> bool:
    return any(_file_eq(needle, h) for h in haystack)


def _normalize(file: str) -> str:
    """Strip a single leading './' but preserve everything else.

    Crucially, this does NOT call Path.resolve() — comparing anchors must
    not depend on the caller's current working directory.
    """
    s = file.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _suffix_match(short: str, long: str) -> bool:
    """True when `short` is a path-component-aligned suffix of `long`."""
    if not short or not long or len(short) >= len(long):
        return False
    if not long.endswith(short):
        return False
    boundary_char = long[-len(short) - 1]
    return boundary_char == "/"

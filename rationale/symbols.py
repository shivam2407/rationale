"""Lightweight symbolic anchoring.

v1's goal: make decisions survive refactors. Line numbers move; symbol
names don't. When a decision is anchored to a function `PaymentService.retry`,
we want to find it again after the function drifts 40 lines down the file.

We intentionally do NOT depend on tree-sitter yet. Python ships with an
`ast` module that handles the most common language well. For JS/TS/Go/Rust
we use narrow regex extractors — not perfect, but they handle 80% of real
code without adding a 100MB native dependency. A tree-sitter backend can
slot in behind this same interface later.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Symbol:
    """A named code location extracted from a source file."""

    name: str
    line_start: int
    line_end: int


# --- Public API -------------------------------------------------------------


def extract_symbols(path: Path | str) -> list[Symbol]:
    """Return all top-level and nested symbols defined in `path`.

    Falls back to [] when the file is missing, binary, or in an unsupported
    language. Never raises.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    ext = p.suffix.lower()
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        return []
    try:
        return extractor(text)
    except Exception:  # pragma: no cover - extractor must never crash callers
        return []


def find_symbol(path: Path | str, name: str) -> Symbol | None:
    """Locate the first symbol matching `name` (exact, then dotted suffix)."""
    if not name:
        return None
    syms = extract_symbols(path)
    for s in syms:
        if s.name == name:
            return s
    # Handle renames inside a class: "Beta.gamma" still matches "gamma"
    tail = name.rsplit(".", 1)[-1]
    for s in syms:
        if s.name == tail or s.name.endswith("." + tail):
            return s
    return None


def symbol_at_line(path: Path | str, line: int) -> Symbol | None:
    """Return the innermost symbol enclosing `line`, if any."""
    syms = extract_symbols(path)
    candidates = [s for s in syms if s.line_start <= line <= s.line_end]
    if not candidates:
        return None
    # Innermost = smallest span
    return min(candidates, key=lambda s: s.line_end - s.line_start)


def content_hash(text: str) -> str:
    """Stable fingerprint of a code snippet.

    Normalizes trailing whitespace per line so autoformatters don't mark
    every anchor stale. Line endings are normalized to \n, leading/trailing
    blank lines are stripped.
    """
    normalized = _normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def hash_file_range(
    path: Path | str, line_start: int, line_end: int
) -> str | None:
    """Hash the content of `path` between `line_start` and `line_end` (inclusive)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    if not lines:
        return None
    start = max(1, line_start)
    end = min(len(lines), line_end)
    if start > end:
        return None
    snippet = "\n".join(lines[start - 1 : end])
    return content_hash(snippet)


# --- Internal helpers -------------------------------------------------------


def _normalize_for_hash(text: str) -> str:
    # Normalize CRLF, strip trailing whitespace per line, strip leading/trailing
    # blank lines. Preserve interior blank lines — they carry meaning.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _python_symbols(text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[Symbol] = []

    def walk(node: ast.AST, prefix: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}{node.name}" if prefix else node.name
            line_start = node.lineno
            line_end = getattr(node, "end_lineno", None) or line_start
            out.append(Symbol(name=name, line_start=line_start, line_end=line_end))
            # Use the current symbol's own name as the prefix for its body
            # so methods are captured as Class.method and nested functions
            # as Class.method.nested.
            child_prefix = f"{name}."
            for child in node.body:
                walk(child, prefix=child_prefix)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, prefix)

    for node in tree.body:
        walk(node, prefix="")
    return out


# Regex-based extractors for languages where a real parser would balloon
# the dependency graph. Good enough for top-level functions/classes; misses
# closures and anonymous callbacks by design.

_JS_TS_RE = re.compile(
    r"""
    (?:^|\n)
    \s*
    (?:export\s+)?
    (?:
        (?:async\s+)?function\s+(?P<fn>[A-Za-z_$][\w$]*)      # function foo
      | class\s+(?P<cls>[A-Za-z_$][\w$]*)                     # class Foo
      | (?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*
        (?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>       # const foo = () =>
    )
    """,
    re.VERBOSE,
)

_GO_RE = re.compile(
    r"""
    (?:^|\n)
    \s*
    func\s+
    (?:\([^)]*\)\s*)?                # receiver for methods
    (?P<name>[A-Za-z_][\w]*)
    \s*\(
    """,
    re.VERBOSE,
)

_RUST_RE = re.compile(
    r"""
    (?:^|\n)
    \s*
    (?:pub(?:\([^)]*\))?\s+)?
    (?:async\s+)?
    (?:fn|struct|enum|trait)\s+
    (?P<name>[A-Za-z_][\w]*)
    """,
    re.VERBOSE,
)


def _regex_symbols(text: str, pattern: re.Pattern[str]) -> list[Symbol]:
    out: list[Symbol] = []
    lines = text.splitlines()
    for match in pattern.finditer(text):
        name = _pick_named_group(match)
        if not name:
            continue
        # Lines are 1-indexed in our model
        line_start = text.count("\n", 0, match.start()) + 1
        # End line: best-effort to the next top-level definition or EOF.
        # We can't parse bracket balance without a real parser; a reasonable
        # cap keeps staleness checks useful without over-claiming.
        line_end = min(len(lines), line_start + 200)
        out.append(Symbol(name=name, line_start=line_start, line_end=line_end))
    return out


def _pick_named_group(match: re.Match[str]) -> str | None:
    for key, val in match.groupdict().items():
        if val:
            return val
    return None


Extractor = Callable[[str], list[Symbol]]


_EXTRACTORS: dict[str, Extractor] = {
    ".py": _python_symbols,
    ".js": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".jsx": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".mjs": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".cjs": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".ts": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".tsx": lambda text: _regex_symbols(text, _JS_TS_RE),
    ".go": lambda text: _regex_symbols(text, _GO_RE),
    ".rs": lambda text: _regex_symbols(text, _RUST_RE),
}

"""Capture layer: parse Claude Code transcript JSONL into raw events.

Claude Code's Edit/Write tools only emit `file_path`, `old_string`, and
`new_string` — they do **not** carry line numbers. To produce useful anchors
we read the working-tree file (the Stop hook fires after the edit lands)
and locate the new content. If the file is gone or the text isn't found,
we fall back to lines 1..N.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class FileEdit:
    """A single file edit performed by the agent."""

    file: str
    line_start: int
    line_end: int
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "snippet": self.snippet,
        }


@dataclass
class SessionTrace:
    """Distilled view of a Claude Code session."""

    session_id: str
    agent: str = "claude-code"
    user_prompts: list[str] = field(default_factory=list)
    assistant_text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    edits: list[FileEdit] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

    def to_prompt_payload(self, max_chars: int = 24000) -> str:
        parts: list[str] = [f"# Session {self.session_id}\n"]
        if self.user_prompts:
            parts.append("## User prompts")
            for p in self.user_prompts:
                parts.append(f"- {_truncate(p, 600)}")
        if self.thinking:
            parts.append("\n## Agent thinking")
            for t in self.thinking[-12:]:
                parts.append(_truncate(t, 1200))
        if self.assistant_text:
            parts.append("\n## Agent messages")
            for m in self.assistant_text[-12:]:
                parts.append(_truncate(m, 800))
        if self.edits:
            parts.append("\n## File edits")
            for e in self.edits:
                parts.append(
                    f"- {e.file}:{e.line_start}-{e.line_end} "
                    f"{_truncate(e.snippet, 240)}"
                )
        if self.tool_calls:
            parts.append("\n## Tool calls")
            parts.extend(f"- {t}" for t in self.tool_calls[:30])
        joined = "\n".join(parts)
        if len(joined) > max_chars:
            return joined[:max_chars] + "\n…[truncated]"
        return joined


def parse_transcript(path: Path | str) -> SessionTrace:
    """Read a Claude Code transcript JSONL file into a SessionTrace.

    Accepts multiple shapes (assistant/user/tool_use/tool_result) so external
    trace formats can be swapped in later without a rewrite.
    """
    p = Path(path)
    trace = SessionTrace(session_id=p.stem)
    if not p.exists():
        return trace
    bad_lines = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        _ingest_event(event, trace)
    if bad_lines:
        # Surface (but don't fail) so users notice silently corrupted transcripts.
        import sys as _sys

        print(
            f"rationale: warning: skipped {bad_lines} malformed line(s) in {p}",
            file=_sys.stderr,
        )
    return trace


def _ingest_event(event: dict[str, Any], trace: SessionTrace) -> None:
    sid = event.get("sessionId") or event.get("session_id")
    if sid:
        trace.session_id = str(sid)

    role = event.get("role") or event.get("type")
    message = event.get("message") if isinstance(event.get("message"), dict) else None

    if role == "user":
        text = _extract_text(event.get("content")) or _extract_text(
            message.get("content") if message else None
        )
        if text:
            trace.user_prompts.append(text)
        return

    if role in {"assistant", "agent"}:
        content = event.get("content")
        if message and not content:
            content = message.get("content")
        for block in _iter_blocks(content):
            btype = block.get("type")
            if btype == "thinking":
                t = block.get("thinking") or block.get("text") or ""
                if t:
                    trace.thinking.append(t)
            elif btype == "text":
                t = block.get("text") or ""
                if t:
                    trace.assistant_text.append(t)
            elif btype == "tool_use":
                _ingest_tool_use(block, trace)
        return

    if role == "tool_use":
        _ingest_tool_use(event, trace)


def _ingest_tool_use(block: dict[str, Any], trace: SessionTrace) -> None:
    name = block.get("name") or block.get("tool") or "tool"
    inp = block.get("input") or block.get("arguments") or {}
    if not isinstance(inp, dict):
        trace.tool_calls.append(str(name))
        return

    file_path = inp.get("file_path") or inp.get("path") or inp.get("filePath")
    if name in {"Edit", "Write", "MultiEdit", "NotebookEdit"} and file_path:
        trace.edits.extend(_extract_edits(name, file_path, inp))

    trace.tool_calls.append(f"{name}({_short_args(inp)})")


def _extract_edits(
    name: str, file_path: str, inp: dict[str, Any]
) -> list[FileEdit]:
    """Turn one tool_use into one or more FileEdit records.

    For Write: the line range is the whole new content (1..N).
    For Edit: locate `new_string` in the post-edit working tree.
    For MultiEdit: process each `edits[*]` entry the same way.
    """
    file_path = str(file_path)
    if name == "Write":
        snippet = str(inp.get("content") or "")
        line_start, line_end = _resolve_range(file_path, snippet, fallback_full=True)
        return [
            FileEdit(
                file=file_path,
                line_start=line_start,
                line_end=line_end,
                snippet=snippet,
            )
        ]

    if name == "MultiEdit":
        items = inp.get("edits") or []
        out: list[FileEdit] = []
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                snippet = str(it.get("new_string") or it.get("new_text") or "")
                line_start, line_end = _resolve_range(file_path, snippet)
                out.append(
                    FileEdit(
                        file=file_path,
                        line_start=line_start,
                        line_end=line_end,
                        snippet=snippet,
                    )
                )
        return out

    # Edit, NotebookEdit, and any other line-bearing edit
    snippet = str(inp.get("new_string") or inp.get("new_text") or "")
    # Allow pre-resolved ranges if a future tool ever sends them
    line_start = inp.get("line_start") or inp.get("startLine")
    line_end = inp.get("line_end") or inp.get("endLine")
    if line_start and line_end:
        return [
            FileEdit(
                file=file_path,
                line_start=int(line_start),
                line_end=int(line_end),
                snippet=snippet,
            )
        ]
    line_start, line_end = _resolve_range(file_path, snippet)
    return [
        FileEdit(
            file=file_path,
            line_start=line_start,
            line_end=line_end,
            snippet=snippet,
        )
    ]


def _resolve_range(
    file_path: str, snippet: str, fallback_full: bool = False
) -> tuple[int, int]:
    """Return (line_start, line_end) for `snippet` inside `file_path`.

    `fallback_full` means: if we can read the file but not locate the snippet,
    use the file's full extent (true for Write, where snippet IS the file).
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # File deleted, binary, or not readable — degrade gracefully.
        n = max(1, snippet.count("\n") + 1)
        return (1, n)

    if snippet:
        idx = content.find(snippet)
        if idx >= 0:
            line_start = content.count("\n", 0, idx) + 1
            # Count visible lines in the snippet — trailing newlines are
            # line terminators, not separate lines.
            snippet_lines = max(1, len(snippet.splitlines()))
            line_end = line_start + snippet_lines - 1
            return (line_start, max(line_end, line_start))

    if fallback_full:
        lines = content.splitlines() or [""]
        return (1, len(lines))

    n = max(1, len(snippet.splitlines()) or 1)
    return (1, n)


def _iter_blocks(content: Any) -> Iterable[dict[str, Any]]:
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b
    elif isinstance(content, dict):
        yield content


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    return ""


def _short_args(d: dict[str, Any]) -> str:
    keys = list(d.keys())[:3]
    return ",".join(keys)


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def current_git_sha(repo_root: Path | str = ".") -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None

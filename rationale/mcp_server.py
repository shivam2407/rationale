"""Cross-agent MCP server — exposes decisions to any MCP-aware agent.

Rationale's decision log is valuable to humans via the CLI and to agents
via MCP. An agent on a fresh session can call ``rationale_why`` instead
of rebuilding context from scratch.

Two transport options:

1. **Tool dispatch layer** (this module's top half) — pure Python, no
   external deps. Stable interface used by every transport.
2. **MCP stdio server** (this module's bottom half) — implements a
   narrow JSON-RPC 2.0 subset of the Model Context Protocol so any MCP
   client can spawn ``rationale mcp`` as a subprocess. The full MCP SDK
   (optional ``[mcp]`` extra) layers on top when installed.

Keeping transport optional means the tool dispatch layer stays testable
without subprocess gymnastics.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rationale import __version__
from rationale.anchoring import build_anchor
from rationale.capture import current_git_sha
from rationale.models import Decision
from rationale.query import query
from rationale.rollup import (
    OverallSummary,
    Rollup,
    by_agent,
    by_file,
    by_tag,
    overall_summary,
)
from rationale.staleness import check_decision
from rationale.storage import DecisionStore


class MCPToolError(Exception):
    """Raised when a tool call can't be dispatched (unknown, bad args)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], Path], Any]


# --- Tool handlers ----------------------------------------------------------


def _why_handler(args: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    term = args.get("term")
    if not isinstance(term, str) or not term:
        raise MCPToolError("rationale_why requires a string 'term' argument")
    store = DecisionStore(repo_root)
    hits = query(store, term)
    return [
        {
            "id": h.decision.id,
            "chosen": h.decision.chosen,
            "reasoning": h.decision.reasoning,
            "files": h.decision.files,
            "anchors": [a.to_dict() for a in h.decision.anchors],
            "tags": h.decision.tags,
            "confidence": h.decision.confidence,
            "timestamp": h.decision.timestamp,
            "score": h.score,
            "reason": h.reason,
        }
        for h in hits
    ]


def _list_handler(_args: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    store = DecisionStore(repo_root)
    decisions = sorted(store.all(), key=lambda d: d.timestamp, reverse=True)
    return [
        {
            "id": d.id,
            "chosen": d.chosen,
            "files": d.files,
            "tags": d.tags,
            "confidence": d.confidence,
            "timestamp": d.timestamp,
        }
        for d in decisions
    ]


def _check_handler(_args: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    store = DecisionStore(repo_root)
    return [
        {
            "id": summary.decision.id,
            "status": summary.status.value,
            "files": summary.decision.files,
            "anchor_reports": [
                {
                    "file": r.anchor.file,
                    "status": r.status.value,
                    "current_line_start": r.current_line_start,
                    "current_line_end": r.current_line_end,
                    "detail": r.detail,
                }
                for r in summary.anchor_reports
            ],
        }
        for summary in (check_decision(d, repo_root=store.root) for d in store.all())
    ]


def _record_handler(args: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Runtime capture: write a Decision directly from the agent's
    explicit call, skipping the Stop-hook transcript-distillation path.

    This is the v0.4 primary capture path. The agent calls it whenever
    it makes a non-trivial choice and passes the reasoning in its own
    voice. No post-hoc LLM interpretation, no API key needed.
    """
    chosen = args.get("chosen")
    reasoning = args.get("reasoning")
    raw_files = args.get("files")

    if not isinstance(chosen, str) or not chosen.strip():
        raise MCPToolError("'chosen' must be a non-empty string")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise MCPToolError("'reasoning' must be a non-empty string")
    if not isinstance(raw_files, list) or not raw_files:
        raise MCPToolError("'files' must be a non-empty list of file paths")

    files: list[str] = [f for f in raw_files if isinstance(f, str) and f]
    if not files:
        raise MCPToolError("'files' must contain at least one string path")

    alternatives_raw = args.get("alternatives", [])
    alternatives = (
        [a for a in alternatives_raw if isinstance(a, str)]
        if isinstance(alternatives_raw, list)
        else []
    )

    tags_raw = args.get("tags", [])
    tags = (
        [t for t in tags_raw if isinstance(t, str)]
        if isinstance(tags_raw, list)
        else []
    )

    confidence_raw = args.get("confidence", "medium")
    confidence = (
        confidence_raw
        if isinstance(confidence_raw, str)
        and confidence_raw.lower() in {"low", "medium", "high"}
        else "medium"
    )

    session_id = args.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        session_id = None

    # Build anchors — one per distinct file the decision touches. Line
    # range defaults to the whole file when it exists on disk; if the
    # agent later wants sub-file precision, a future tool param can
    # accept {file, line_start, line_end} objects.
    anchors = []
    seen_files: set[str] = set()
    for f in files:
        if f in seen_files:
            continue
        seen_files.add(f)
        resolved = f
        abs_path = Path(f) if Path(f).is_absolute() else repo_root / f
        try:
            if abs_path.exists():
                text = abs_path.read_text(encoding="utf-8")
                line_count = max(1, len(text.splitlines()) or 1)
            else:
                line_count = 1
        except (OSError, UnicodeDecodeError):
            line_count = 1
        anchors.append(build_anchor(resolved, 1, line_count))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Decision id: stable-ish across retries on the same inputs, unique
    # enough across concurrent records via the timestamp suffix.
    payload = f"{session_id or 'runtime'}|{chosen}|{now}".encode("utf-8")
    decision_id = f"d-{hashlib.sha1(payload).hexdigest()[:8]}"

    decision = Decision(
        id=decision_id,
        timestamp=now,
        agent="claude-code",
        chosen=chosen.strip(),
        reasoning=reasoning.strip(),
        anchors=anchors,
        alternatives=alternatives,
        confidence=confidence,
        tags=tags,
        git_sha=current_git_sha(repo_root),
        session_id=session_id,
    )

    store = DecisionStore(repo_root)
    store.init()
    path = store.save(decision)

    try:
        saved_to = str(path.relative_to(repo_root))
    except ValueError:
        saved_to = str(path)

    return {
        "id": decision.id,
        "saved_to": saved_to,
        "status": "recorded",
    }


def _summary_handler(_args: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    store = DecisionStore(repo_root)
    decisions = store.all()
    overall: OverallSummary = overall_summary(decisions)
    return {
        "total": overall.total,
        "weighted_score": overall.weighted_score,
        "by_confidence": overall.by_confidence,
        "by_file": [_rollup_to_dict(r) for r in by_file(decisions)],
        "by_agent": [_rollup_to_dict(r) for r in by_agent(decisions)],
        "by_tag": [_rollup_to_dict(r) for r in by_tag(decisions)],
    }


def _rollup_to_dict(r: Rollup) -> dict[str, Any]:
    return {
        "key": r.key,
        "count": r.count,
        "weight": round(r.weight, 4),
        "sample_ids": list(r.sample_ids),
    }


# --- Tool registry ----------------------------------------------------------


TOOLS: dict[str, ToolSpec] = {
    "rationale_record": ToolSpec(
        name="rationale_record",
        description=(
            "Record a non-trivial decision you just made. Call this when "
            "you pick one approach over alternatives — choosing a retry "
            "count, selecting a library, structuring an API, naming a "
            "module, splitting a function. Skip trivial edits (typos, "
            "moves, mechanical refactors). The recorded reasoning is what "
            "a future session will see when someone runs `rationale why "
            "<file>:<line>` on the code you touched."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "chosen": {
                    "type": "string",
                    "description": "Short noun phrase for what you picked (e.g. 'fixed 3x retry').",
                },
                "reasoning": {
                    "type": "string",
                    "description": "One paragraph in your own voice — why this choice over the alternatives.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Repo-relative paths the decision affects.",
                    "minItems": 1,
                },
                "alternatives": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Other options you considered and rejected.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "How confident you are in the call (default: medium).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short topical tags (e.g. ['reliability', 'payments']).",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional Claude Code session id; lets the Stop hook skip redundant distillation.",
                },
            },
            "required": ["chosen", "reasoning", "files"],
        },
        handler=_record_handler,
    ),
    "rationale_why": ToolSpec(
        name="rationale_why",
        description=(
            "Look up decisions anchored to a file:line, a file path, or "
            "matching a free-text term."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "File:line, file path, or search term.",
                }
            },
            "required": ["term"],
        },
        handler=_why_handler,
    ),
    "rationale_list": ToolSpec(
        name="rationale_list",
        description="List every captured decision, newest first.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_handler,
    ),
    "rationale_check": ToolSpec(
        name="rationale_check",
        description=(
            "Classify every decision against the current working tree: "
            "FRESH / DRIFTED / STALE / MISSING / UNKNOWN."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_check_handler,
    ),
    "rationale_summary": ToolSpec(
        name="rationale_summary",
        description=(
            "Confidence-weighted rollup across files, agents, and tags."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_summary_handler,
    ),
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in TOOLS.values()
    ]


def dispatch_tool(
    name: str | None, arguments: Any, *, repo_root: Path | str
) -> Any:
    if not isinstance(name, str) or not name:
        raise MCPToolError("tool name is required and must be a string")
    tool = TOOLS.get(name)
    if tool is None:
        raise MCPToolError(f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise MCPToolError(
            f"tool {name} requires an object arguments payload, got {type(arguments).__name__}"
        )
    return tool.handler(arguments, Path(repo_root))


# --- JSON-RPC stdio transport ----------------------------------------------


def serve_stdio(
    repo_root: Path | str = ".",
    *,
    stdin: Any = None,
    stdout: Any = None,
) -> None:
    """Run a minimal MCP-compatible JSON-RPC server over stdio.

    Supports ``initialize``, ``tools/list``, and ``tools/call``. This is
    not a full MCP implementation — but it is enough for a Claude Desktop
    or MCP-aware agent to list and invoke rationale tools. The full SDK
    sits behind the ``[mcp]`` extra.

    The ``stdin``/``stdout`` parameters exist so the transport loop can
    be exercised by unit tests; in production they default to the
    real process streams.
    """
    root = Path(repo_root)
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        request: Any = None
        try:
            request = json.loads(line)
            response = _handle_request(request, root)
        except json.JSONDecodeError:
            response = _error_response(None, -32700, "Parse error")
        except Exception as exc:  # noqa: BLE001 - transport-level catch-all
            # Do NOT leak the exception text to the client — a networked
            # MCP transport would happily echo file paths and stack hints
            # to whoever is listening. Log locally, return a generic code.
            print(
                f"rationale: mcp internal error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            response = _error_response(
                request.get("id") if isinstance(request, dict) else None,
                -32603,
                "Internal error",
            )
        if response is not None:
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()


def _handle_request(
    request: Any, root: Path
) -> dict[str, Any] | None:
    """Produce a JSON-RPC response for a single request envelope.

    Returns None for valid notifications (requests without an ``id``).
    Per JSON-RPC 2.0 §4.1, notifications must never receive a response,
    regardless of which method they target. Invalid envelopes produce an
    error response. Separated from serve_stdio so the transport loop
    stays trivial and the business logic is directly testable.
    """
    if not isinstance(request, dict):
        return _error_response(None, -32600, "Invalid Request")
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    # Notifications: JSON-RPC 2.0 requires no response for ANY method
    # that is invoked without an ``id``. Handle this up front, before
    # dispatching to any method branch.
    if req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "rationale", "version": __version__},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_tools()},
        }

    if method == "tools/call":
        if not isinstance(params, dict):
            return _error_response(req_id, -32602, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            value = dispatch_tool(name, arguments, repo_root=root)
        except MCPToolError as exc:
            return _error_response(req_id, -32602, str(exc))
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(value, indent=2)}
                ]
            },
        }

    return _error_response(req_id, -32601, f"method not found: {method}")


def _error_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }

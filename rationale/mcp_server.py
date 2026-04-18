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

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rationale import __version__
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
    name: str, arguments: Any, *, repo_root: Path | str
) -> Any:
    tool = TOOLS.get(name)
    if tool is None:
        raise MCPToolError(f"unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise MCPToolError(
            f"tool {name} requires an object arguments payload, got {type(arguments).__name__}"
        )
    return tool.handler(arguments, Path(repo_root))


# --- JSON-RPC stdio transport ----------------------------------------------


def serve_stdio(repo_root: Path | str = ".") -> None:  # pragma: no cover - IO loop
    """Run a minimal MCP-compatible JSON-RPC server over stdio.

    Supports ``initialize``, ``tools/list``, and ``tools/call``. This is
    not a full MCP implementation — but it is enough for a Claude Desktop
    or MCP-aware agent to list and invoke rationale tools. The full SDK
    sits behind the ``[mcp]`` extra.
    """
    root = Path(repo_root)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = _handle_request(request, root)
        except json.JSONDecodeError:
            response = _error_response(None, -32700, "Parse error")
        except Exception as exc:  # noqa: BLE001 - transport-level catch-all
            response = _error_response(
                request.get("id") if isinstance(request, dict) else None,
                -32603,
                f"Internal error: {exc}",
            )
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def _handle_request(
    request: dict[str, Any], root: Path
) -> dict[str, Any] | None:  # pragma: no cover - IO loop
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

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

    # Notification (no id): ignore silently
    if req_id is None:
        return None
    return _error_response(req_id, -32601, f"method not found: {method}")


def _error_response(
    req_id: Any, code: int, message: str
) -> dict[str, Any]:  # pragma: no cover - IO loop
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }

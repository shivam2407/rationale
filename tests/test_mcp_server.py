"""Tests for the MCP server tool dispatch layer.

The actual stdio transport is tested via subprocess integration in a
separate test; here we exercise the pure dispatch layer that any transport
(stdio, HTTP, websocket) delegates to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rationale.mcp_server import (
    TOOLS,
    MCPToolError,
    dispatch_tool,
    list_tools,
)
from rationale.models import Decision, DecisionAnchor
from rationale.storage import DecisionStore


@pytest.fixture
def populated_store(tmp_path: Path) -> DecisionStore:
    store = DecisionStore(tmp_path)
    store.init()
    d = Decision(
        id="d-abc",
        timestamp="2026-04-17T00:00:00Z",
        agent="claude-code",
        chosen="retry 3x",
        reasoning="downstream rate limits cap traffic",
        anchors=[
            DecisionAnchor(
                file="src/payment.py",
                line_start=1,
                line_end=10,
                symbol="PaymentService.retry",
            )
        ],
        tags=["reliability"],
        confidence="medium",
    )
    store.save(d)
    return store


def test_list_tools_includes_core_actions() -> None:
    names = {t["name"] for t in list_tools()}
    # Every tool the CLI exposes is also an MCP tool so agents can query
    # the decision log the same way humans do. rationale_record is the
    # runtime capture tool — the primary path in v0.4.
    assert {
        "rationale_why",
        "rationale_list",
        "rationale_check",
        "rationale_summary",
        "rationale_record",
    }.issubset(names)


def test_every_tool_has_schema() -> None:
    for t in list_tools():
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t
        assert t["inputSchema"].get("type") == "object"


def test_dispatch_why_by_line(populated_store: DecisionStore) -> None:
    result = dispatch_tool(
        "rationale_why",
        {"term": "src/payment.py:5"},
        repo_root=populated_store.root,
    )
    assert isinstance(result, list)
    assert result
    assert result[0]["chosen"] == "retry 3x"


def test_dispatch_why_text_search(populated_store: DecisionStore) -> None:
    result = dispatch_tool(
        "rationale_why",
        {"term": "retry"},
        repo_root=populated_store.root,
    )
    assert result
    assert result[0]["id"] == "d-abc"


def test_dispatch_list_returns_all(populated_store: DecisionStore) -> None:
    result = dispatch_tool(
        "rationale_list", {}, repo_root=populated_store.root
    )
    assert len(result) == 1
    assert result[0]["id"] == "d-abc"


def test_dispatch_check_reports_status(populated_store: DecisionStore) -> None:
    result = dispatch_tool(
        "rationale_check", {}, repo_root=populated_store.root
    )
    assert isinstance(result, list)
    assert result
    assert "status" in result[0]


def test_dispatch_summary_returns_rollups(populated_store: DecisionStore) -> None:
    result = dispatch_tool(
        "rationale_summary", {}, repo_root=populated_store.root
    )
    assert "total" in result
    assert result["total"] >= 1
    assert "by_file" in result


def test_dispatch_record_writes_decision(tmp_path: Path) -> None:
    """Runtime capture: the tool writes a full Decision to .rationale/ with
    the reasoning the agent provided — no distillation LLM involved."""
    store = DecisionStore(tmp_path)
    store.init()

    src = tmp_path / "pay.py"
    src.write_text("def charge():\n    return 3\n", encoding="utf-8")

    result = dispatch_tool(
        "rationale_record",
        {
            "chosen": "fixed 3x retry",
            "alternatives": ["exponential backoff", "circuit breaker"],
            "reasoning": (
                "Downstream rate limits already cap traffic; exponential "
                "backoff stretches p95 past SLO."
            ),
            "files": ["pay.py"],
            "confidence": "medium",
            "tags": ["reliability"],
        },
        repo_root=tmp_path,
    )
    assert result["status"] == "recorded"
    assert result["id"].startswith("d-")

    # Round-trip: the decision must load back with the agent's actual
    # reasoning text — not "No reasoning trace captured".
    stored = store.all()
    assert len(stored) == 1
    d = stored[0]
    assert d.chosen == "fixed 3x retry"
    assert "rate limits" in d.reasoning
    assert "exponential backoff" in d.alternatives
    assert d.confidence == "medium"
    assert "reliability" in d.tags


def test_dispatch_record_accepts_session_id(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.init()
    dispatch_tool(
        "rationale_record",
        {
            "chosen": "x",
            "reasoning": "because",
            "files": ["mod.py"],
            "session_id": "sess-42",
        },
        repo_root=tmp_path,
    )
    [d] = store.all()
    assert d.session_id == "sess-42"


def test_dispatch_record_rejects_missing_chosen(tmp_path: Path) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(
            "rationale_record",
            {"reasoning": "r", "files": ["x.py"]},
            repo_root=tmp_path,
        )


def test_dispatch_record_rejects_missing_reasoning(tmp_path: Path) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(
            "rationale_record",
            {"chosen": "x", "files": ["x.py"]},
            repo_root=tmp_path,
        )


def test_dispatch_record_rejects_missing_files(tmp_path: Path) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(
            "rationale_record",
            {"chosen": "x", "reasoning": "r", "files": []},
            repo_root=tmp_path,
        )


def test_dispatch_record_normalizes_invalid_confidence(tmp_path: Path) -> None:
    """An agent that passes a bogus confidence string should still get a
    saved decision (falling back to medium) rather than a hard error."""
    store = DecisionStore(tmp_path)
    store.init()
    dispatch_tool(
        "rationale_record",
        {
            "chosen": "x",
            "reasoning": "r",
            "files": ["x.py"],
            "confidence": "extremely-high",
        },
        repo_root=tmp_path,
    )
    [d] = store.all()
    assert d.confidence == "medium"


def test_dispatch_record_drops_non_string_alternatives(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.init()
    dispatch_tool(
        "rationale_record",
        {
            "chosen": "x",
            "reasoning": "r",
            "files": ["x.py"],
            "alternatives": ["ok", 42, None, "also ok"],
        },
        repo_root=tmp_path,
    )
    [d] = store.all()
    assert d.alternatives == ["ok", "also ok"]


def test_dispatch_record_attaches_git_sha_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record tool attaches the current git SHA so the decision is
    linked to a specific code state — same semantics as transcript
    distillation."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    store = DecisionStore(tmp_path)
    store.init()
    dispatch_tool(
        "rationale_record",
        {"chosen": "x", "reasoning": "r", "files": ["x.py"]},
        repo_root=tmp_path,
    )
    [d] = store.all()
    assert d.git_sha is not None
    assert len(d.git_sha) >= 7


def test_dispatch_rejects_unknown_tool(populated_store: DecisionStore) -> None:
    with pytest.raises(MCPToolError) as exc:
        dispatch_tool("no_such_tool", {}, repo_root=populated_store.root)
    assert "unknown" in str(exc.value).lower()


def test_dispatch_rejects_non_dict_arguments(populated_store: DecisionStore) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(
            "rationale_why", ["bad"], repo_root=populated_store.root
        )


def test_dispatch_missing_required_argument(
    populated_store: DecisionStore,
) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(
            "rationale_why", {}, repo_root=populated_store.root
        )


def test_tools_registry_matches_list_tools() -> None:
    registry_names = set(TOOLS)
    listed_names = {t["name"] for t in list_tools()}
    assert registry_names == listed_names


# --- JSON-RPC transport tests ----------------------------------------------

import io
import json

from rationale.mcp_server import _handle_request, serve_stdio


def test_handle_request_initialize() -> None:
    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        Path("."),
    )
    assert resp is not None
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "rationale"


def test_handle_request_tools_list() -> None:
    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        Path("."),
    )
    assert resp is not None
    assert "tools" in resp["result"]
    assert any(t["name"] == "rationale_why" for t in resp["result"]["tools"])


def test_handle_request_unknown_method_returns_method_not_found() -> None:
    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "bogus/method"},
        Path("."),
    )
    assert resp is not None
    assert resp["error"]["code"] == -32601


def test_handle_request_tools_call_rejects_bad_tool(
    populated_store: DecisionStore,
) -> None:
    resp = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        },
        populated_store.root,
    )
    assert resp is not None
    assert resp["error"]["code"] == -32602


def test_handle_request_tools_call_happy_path(
    populated_store: DecisionStore,
) -> None:
    resp = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "rationale_why",
                "arguments": {"term": "retry"},
            },
        },
        populated_store.root,
    )
    assert resp is not None
    assert "result" in resp
    content = resp["result"]["content"][0]
    assert content["type"] == "text"
    payload = json.loads(content["text"])
    assert payload[0]["id"] == "d-abc"


def test_handle_request_notification_returns_none() -> None:
    # A notification has no `id` and must not produce a response envelope.
    resp = _handle_request(
        {"jsonrpc": "2.0", "method": "some/notification"},
        Path("."),
    )
    assert resp is None


def test_handle_request_non_dict_returns_invalid_request() -> None:
    resp = _handle_request("not a dict", Path("."))  # type: ignore[arg-type]
    assert resp is not None
    assert resp["error"]["code"] == -32600


def test_serve_stdio_parse_error_for_malformed_json(tmp_path: Path) -> None:
    stdin = io.StringIO("this is not json\n")
    stdout = io.StringIO()
    serve_stdio(tmp_path, stdin=stdin, stdout=stdout)
    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert response["error"]["code"] == -32700


def test_serve_stdio_routes_tools_list(tmp_path: Path) -> None:
    req = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    stdin = io.StringIO(req + "\n")
    stdout = io.StringIO()
    serve_stdio(tmp_path, stdin=stdin, stdout=stdout)
    response = json.loads(stdout.getvalue().strip())
    assert response["id"] == 7
    assert "tools" in response["result"]


def test_serve_stdio_skips_blank_lines(tmp_path: Path) -> None:
    stdin = io.StringIO("\n\n\n")
    stdout = io.StringIO()
    serve_stdio(tmp_path, stdin=stdin, stdout=stdout)
    assert stdout.getvalue() == ""


def test_serve_stdio_internal_error_returns_minus_32603_without_leaking_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An internal exception during request handling must produce a -32603
    response with a generic message — never the exception text."""
    from rationale import mcp_server

    def boom(_request: object, _root: object) -> None:
        raise RuntimeError("sensitive internal detail: /etc/passwd")

    monkeypatch.setattr(mcp_server, "_handle_request", boom)

    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "initialize"}) + "\n"
    )
    stdout = io.StringIO()
    serve_stdio(tmp_path, stdin=stdin, stdout=stdout)
    response = json.loads(stdout.getvalue().strip())
    assert response["id"] == 9
    assert response["error"]["code"] == -32603
    # The exception text must NOT appear in the wire response — it's
    # logged server-side instead (to stderr).
    assert "/etc/passwd" not in response["error"]["message"]
    assert "sensitive internal detail" not in response["error"]["message"]
    captured = capsys.readouterr()
    assert "sensitive internal detail" in captured.err


def test_notification_to_initialize_returns_none() -> None:
    """JSON-RPC 2.0 §4.1: notifications (no id) must never receive a
    response, regardless of which method they target."""
    resp = _handle_request(
        {"jsonrpc": "2.0", "method": "initialize"},
        Path("."),
    )
    assert resp is None


def test_notification_to_tools_list_returns_none() -> None:
    resp = _handle_request(
        {"jsonrpc": "2.0", "method": "tools/list"},
        Path("."),
    )
    assert resp is None


def test_dispatch_tool_rejects_non_string_name(
    populated_store: DecisionStore,
) -> None:
    with pytest.raises(MCPToolError):
        dispatch_tool(None, {}, repo_root=populated_store.root)  # type: ignore[arg-type]
    with pytest.raises(MCPToolError):
        dispatch_tool("", {}, repo_root=populated_store.root)

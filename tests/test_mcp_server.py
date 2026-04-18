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
    # the decision log the same way humans do.
    assert {"rationale_why", "rationale_list", "rationale_check", "rationale_summary"}.issubset(names)


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

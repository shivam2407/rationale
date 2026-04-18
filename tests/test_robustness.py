"""Regression tests for Stop-hook safety: exit codes, LLM error fallback, install-hook wording."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rationale.capture import SessionTrace, FileEdit
from rationale.cli import main
from rationale.distiller import Distiller


class _BoomClient:
    def complete(self, system: str, user: str, model: str) -> str:
        raise RuntimeError("network exploded")


class _EmptyClient:
    def complete(self, system: str, user: str, model: str) -> str:
        return "[]"


def test_distill_falls_back_on_llm_failure() -> None:
    """A transient LLM error must not crash the Stop hook.

    It must degrade to the heuristic distiller so the session's rationale
    is still captured.
    """
    trace = SessionTrace(
        session_id="s",
        thinking=["LRU over TTL because bounded memory matters."],
        edits=[FileEdit("src/x.py", 1, 5, "x")],
    )
    decisions = Distiller(client=_BoomClient(), git_sha="deadbeef").distill(trace)
    assert len(decisions) == 1
    assert decisions[0].confidence == "low"
    assert "heuristic" in decisions[0].tags


def test_distill_falls_back_when_llm_returns_empty_array() -> None:
    """When the LLM returns [] but the session had real signal, don't drop it."""
    trace = SessionTrace(
        session_id="s",
        thinking=["Actually picked LRU."],
        edits=[FileEdit("src/x.py", 1, 5, "x")],
    )
    decisions = Distiller(client=_EmptyClient()).distill(trace)
    assert len(decisions) == 1
    assert "heuristic" in decisions[0].tags


def test_install_hook_bare_emits_only_stop_array(tmp_path: Path) -> None:
    """--bare prints just the inner Stop array for easy merging."""
    runner = CliRunner()
    result = runner.invoke(main, ["install-hook", "--bare"])
    assert result.exit_code == 0
    first_bracket = result.output.index("[")
    last_bracket = result.output.rindex("]")
    parsed = json.loads(result.output[first_bracket : last_bracket + 1])
    assert isinstance(parsed, list)
    assert parsed[0]["matcher"] == "*"
    assert parsed[0]["hooks"][0]["type"] == "command"


def test_install_hook_default_mentions_merge_strategy() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["install-hook"])
    assert result.exit_code == 0
    # Users with existing hooks must see the merge guidance.
    assert "Stop" in result.output
    assert "hooks" in result.output.lower()


def test_capture_exit_code_is_non_blocking(tmp_path: Path) -> None:
    """Stop hooks MUST NOT exit with code 2 (which tells Claude Code to resume)."""
    runner = CliRunner()
    result = runner.invoke(main, ["capture", "--path", str(tmp_path)], input="")
    assert result.exit_code != 2, (
        "exit 2 is Claude Code's 'block the Stop' signal — the agent would "
        "loop instead of stopping."
    )


def test_capture_exit_code_is_non_blocking_with_invalid_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["capture", "--path", str(tmp_path)], input="not json at all"
    )
    assert result.exit_code != 2

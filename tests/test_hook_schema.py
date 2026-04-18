"""Contract tests for the Claude Code Stop-hook config shape.

A previous version of `install-hook` emitted a flat
`[{command, description}]` shape that Claude Code silently ignores. These
tests lock in the real schema (`{matcher, hooks: [{type, command}]}`).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from rationale.cli import build_hook_config, main


def test_build_hook_config_has_stop_matcher_and_nested_hooks() -> None:
    cfg = build_hook_config()
    assert "hooks" in cfg
    assert "Stop" in cfg["hooks"]
    stop = cfg["hooks"]["Stop"]
    assert isinstance(stop, list) and len(stop) == 1
    entry = stop[0]
    assert entry["matcher"] == "*"
    nested = entry["hooks"]
    assert isinstance(nested, list) and len(nested) == 1
    assert nested[0]["type"] == "command"
    cmd = nested[0]["command"]
    assert "rationale capture" in cmd
    assert "--quiet" in cmd


def test_install_hook_cli_prints_valid_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["install-hook", "--path", str(tmp_path)])
    assert result.exit_code == 0
    # Output has a leading instruction line, then the JSON.
    first_brace = result.output.index("{")
    last_brace = result.output.rindex("}")
    parsed = json.loads(result.output[first_brace : last_brace + 1])
    # Same contract as build_hook_config.
    assert parsed["hooks"]["Stop"][0]["hooks"][0]["type"] == "command"

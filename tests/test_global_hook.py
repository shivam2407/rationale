"""The Stop-hook command must respect the current project's cwd.

Regression: an earlier version hardcoded --path <install-time repo> into
the global hook, so every Claude Code session wrote decisions into the
install-time repo regardless of which project the user was actually in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rationale.cli import build_hook_config, main


def test_hook_command_does_not_pin_path() -> None:
    cfg = build_hook_config()
    cmd = cfg["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "--path" not in cmd, (
        "The global hook must not hardcode a repo path — it pins decisions "
        "to one repo and breaks multi-project use."
    )
    assert cmd == "rationale capture --quiet"


def test_capture_reads_cwd_from_stop_hook_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates Claude Code firing the Stop hook from inside project-A.

    Even if the global hook config has no --path, capture must resolve to
    the project's cwd (via the Stop-hook `cwd` field) so decisions land
    there, not in the user's shell cwd.
    """
    project_a = tmp_path / "project-a"
    project_a.mkdir()
    src = project_a / "x.py"
    src.write_text("one\ntwo\nthree\n", encoding="utf-8")

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {
                                "file_path": str(src),
                                "old_string": "two",
                                "new_string": "two",
                            },
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = json.dumps(
        {"transcript_path": str(transcript), "cwd": str(project_a)}
    )

    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    runner = CliRunner()
    # No --path arg; the hook should pick up cwd from the stdin payload.
    result = runner.invoke(main, ["capture"], input=payload)
    assert result.exit_code == 0, result.output
    rationale_dir = project_a / ".rationale"
    assert rationale_dir.is_dir()
    assert list(rationale_dir.rglob("d-*.md")), "no decision written into project-a"


def test_capture_reads_cwd_from_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    src = project_b / "y.py"
    src.write_text("a\nb\n", encoding="utf-8")

    transcript = tmp_path / "t2.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {
                                "file_path": str(src),
                                "old_string": "a",
                                "new_string": "a",
                            },
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_b))

    runner = CliRunner()
    result = runner.invoke(main, ["capture", "--transcript", str(transcript)])
    assert result.exit_code == 0, result.output
    assert list((project_b / ".rationale").rglob("d-*.md"))

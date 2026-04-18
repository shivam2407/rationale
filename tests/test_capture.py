"""Tests for the transcript capture layer."""

from __future__ import annotations

import json
from pathlib import Path

from rationale.capture import (
    FileEdit,
    SessionTrace,
    current_git_sha,
    parse_transcript,
)


def test_parse_transcript_extracts_thinking_text_edits(transcript_file: Path) -> None:
    trace = parse_transcript(transcript_file)
    assert trace.session_id == "sess-001"
    assert any("retries" in t for t in trace.thinking)
    assert any("fixed 3x" in t for t in trace.assistant_text)
    assert trace.user_prompts == ["Add retry logic to PaymentService"]
    assert len(trace.edits) == 1
    edit = trace.edits[0]
    # fixture file is tmp_path/src/payment.ts with 44 lines; new_string
    # starts on line 42, spans through line 44.
    assert edit.file.endswith("src/payment.ts")
    assert edit.line_start == 42
    assert edit.line_end == 44


def test_parse_transcript_handles_missing_file(tmp_path: Path) -> None:
    trace = parse_transcript(tmp_path / "no-such-file.jsonl")
    assert trace.session_id == "no-such-file"
    assert trace.thinking == []
    assert trace.edits == []


def test_parse_transcript_skips_invalid_json_lines(tmp_path: Path) -> None:
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        "\n".join(
            [
                "{not json}",
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "s-2",
                        "content": "hello",
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    trace = parse_transcript(p)
    assert trace.user_prompts == ["hello"]


def test_parse_transcript_handles_string_content_for_user(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps({"type": "user", "content": "raw"}) + "\n",
        encoding="utf-8",
    )
    trace = parse_transcript(p)
    assert trace.user_prompts == ["raw"]


def test_parse_transcript_handles_list_text_for_user(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "user",
                "content": [{"type": "text", "text": "hello"}, "world"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace = parse_transcript(p)
    assert "hello" in trace.user_prompts[0]
    assert "world" in trace.user_prompts[0]


def test_parse_transcript_records_non_edit_tool_calls(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace = parse_transcript(p)
    assert trace.edits == []
    assert any("Bash" in t for t in trace.tool_calls)


def test_to_prompt_payload_truncates_when_huge() -> None:
    trace = SessionTrace(
        session_id="s",
        thinking=["x" * 1100 for _ in range(20)],
        assistant_text=["y" * 700 for _ in range(20)],
    )
    out = trace.to_prompt_payload(max_chars=2000)
    assert len(out) <= 2100
    assert "truncated" in out


def test_to_prompt_payload_individual_chunks_capped() -> None:
    trace = SessionTrace(
        session_id="s",
        thinking=["x" * 50000],
    )
    out = trace.to_prompt_payload(max_chars=10000)
    # one giant thinking entry should be clipped per-entry, not dropped
    assert "x" in out
    assert len(out) < 5000


def test_to_prompt_payload_includes_sections() -> None:
    trace = SessionTrace(
        session_id="s",
        user_prompts=["do it"],
        thinking=["because"],
        assistant_text=["done"],
        edits=[FileEdit("a.py", 1, 2, "x")],
        tool_calls=["Read(file_path)"],
    )
    out = trace.to_prompt_payload()
    assert "User prompts" in out
    assert "Agent thinking" in out
    assert "Agent messages" in out
    assert "File edits" in out
    assert "Tool calls" in out


def test_current_git_sha_returns_none_outside_repo(tmp_path: Path) -> None:
    sha = current_git_sha(tmp_path)
    # tmp_path is not a git repo — must explicitly be None
    assert sha is None


def test_current_git_sha_roundtrips_in_real_repo(tmp_path: Path) -> None:
    import subprocess

    for argv in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "--allow-empty", "-m", "init", "-q"],
    ):
        r = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
        if r.returncode != 0:
            import pytest
            pytest.skip(f"git not usable in this env: {r.stderr}")
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_git_sha(tmp_path) == expected


def test_edit_tool_with_real_claude_code_shape(tmp_path: Path) -> None:
    """Claude Code's Edit emits only file_path + old_string + new_string.

    This is a regression test: a previous version read non-existent
    line_start/line_end keys from Edit inputs, so every real edit
    collapsed to line 1.
    """
    src = tmp_path / "x.py"
    src.write_text(
        "\n".join(["a", "b", "c", "HIT", "e", "f"]) + "\n",
        encoding="utf-8",
    )
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": str(src),
                        "old_string": "HIT",
                        "new_string": "HIT",
                    },
                }
            ]
        },
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(event) + "\n", encoding="utf-8")
    trace = parse_transcript(p)
    assert len(trace.edits) == 1
    assert trace.edits[0].line_start == 4
    assert trace.edits[0].line_end == 4


def test_write_tool_anchors_full_file(tmp_path: Path) -> None:
    src = tmp_path / "new.py"
    content = "line1\nline2\nline3\n"
    src.write_text(content, encoding="utf-8")
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": str(src), "content": content},
                }
            ]
        },
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(event) + "\n", encoding="utf-8")
    trace = parse_transcript(p)
    assert len(trace.edits) == 1
    assert trace.edits[0].line_start == 1
    # Trailing newline must not inflate line_end by one.
    assert trace.edits[0].line_end == 3


def test_multiedit_tool_splits_into_multiple_edits(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text("A\nB\nC\nD\n", encoding="utf-8")
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "MultiEdit",
                    "input": {
                        "file_path": str(src),
                        "edits": [
                            {"old_string": "A", "new_string": "A"},
                            {"old_string": "C", "new_string": "C"},
                        ],
                    },
                }
            ]
        },
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(event) + "\n", encoding="utf-8")
    trace = parse_transcript(p)
    assert len(trace.edits) == 2
    assert trace.edits[0].line_start == 1
    assert trace.edits[1].line_start == 3


def test_edit_falls_back_when_file_missing(tmp_path: Path) -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": str(tmp_path / "ghost.py"),
                        "old_string": "a",
                        "new_string": "a\nb\nc",
                    },
                }
            ]
        },
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(event) + "\n", encoding="utf-8")
    trace = parse_transcript(p)
    assert len(trace.edits) == 1
    assert trace.edits[0].line_start == 1
    assert trace.edits[0].line_end == 3

"""Shared fixtures for the rationale test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rationale.capture import FileEdit, SessionTrace


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def transcript_file(tmp_path: Path) -> Path:
    """A transcript that mirrors Claude Code's real Stop-hook shape.

    Edit tool inputs carry only file_path + old_string + new_string — no
    line numbers. The file on disk is what tells us where the edit landed.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    payment_path = src_dir / "payment.ts"
    # 10 lines of preamble so the edit lands on a realistic mid-file range.
    lines = [f"// line {i}" for i in range(1, 42)]
    lines.append("for (let i = 0; i < 3; i++) {")
    lines.append("  // retry")
    lines.append("}")
    payment_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    events = [
        {
            "type": "user",
            "sessionId": "sess-001",
            "content": "Add retry logic to PaymentService",
        },
        {
            "type": "assistant",
            "sessionId": "sess-001",
            "message": {
                "content": [
                    {
                        "type": "thinking",
                        "thinking": (
                            "Three options for retries: exponential backoff, "
                            "fixed 3x, circuit breaker. Picking fixed 3x: "
                            "downstream rate limits already cap traffic and "
                            "exponential backoff would stretch p95 too far."
                        ),
                    },
                    {
                        "type": "text",
                        "text": "I'll add a fixed 3x retry policy.",
                    },
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {
                            "file_path": str(payment_path),
                            "old_string": "// line 41",
                            "new_string": "for (let i = 0; i < 3; i++) {\n  // retry\n}",
                        },
                    },
                ]
            },
        },
    ]
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def trace_with_edits() -> SessionTrace:
    return SessionTrace(
        session_id="sess-edits",
        thinking=["Picking fixed 3x retry over exponential backoff."],
        assistant_text=["Done."],
        edits=[FileEdit("src/payment.ts", 42, 58, "for (...) retry")],
    )


@pytest.fixture
def empty_trace() -> SessionTrace:
    return SessionTrace(session_id="sess-empty")

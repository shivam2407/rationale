"""End-to-end CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rationale.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_init(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["init", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".rationale").is_dir()
    assert (tmp_path / ".rationale" / "README.md").exists()


def test_capture_with_transcript_offline(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "captured" in result.output
    files = list((tmp_path / ".rationale").rglob("d-*.md"))
    assert len(files) >= 1


def test_capture_via_stdin_payload(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    payload = json.dumps({"transcript_path": str(transcript_file)})
    result = runner.invoke(
        main,
        ["capture", "--path", str(tmp_path)],
        input=payload,
    )
    assert result.exit_code == 0, result.output
    assert "captured" in result.output


def test_capture_errors_when_no_transcript(
    runner: CliRunner, tmp_path: Path
) -> None:
    result = runner.invoke(
        main, ["capture", "--path", str(tmp_path)], input=""
    )
    # Exit 1 (not 2) so Claude Code's Stop hook doesn't resume the agent.
    assert result.exit_code == 1


def test_why_no_results(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(main, ["init", "--path", str(tmp_path)])
    result = runner.invoke(
        main, ["why", "src/x.py:1", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "no decisions" in result.output


def test_why_human_output_after_capture(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )
    result = runner.invoke(
        main,
        ["why", "src/payment.ts:50", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "src/payment.ts" in result.output


def test_why_json_output(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )
    result = runner.invoke(
        main,
        ["why", "src/payment.ts:50", "--json", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    # Real Claude Code transcripts carry absolute paths, so the stored
    # anchor is absolute; the relative query still matches it via the
    # path-component-aligned suffix rule.
    assert parsed[0]["files"][0].endswith("src/payment.ts")


def test_list_empty(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["list", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "no decisions" in result.output


def test_list_after_capture(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )
    result = runner.invoke(main, ["list", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "d-" in result.output


def test_install_hook_prints_snippet(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(main, ["install-hook", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "Stop" in result.output
    assert "rationale capture" in result.output


def test_check_empty_store(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(main, ["init", "--path", str(tmp_path)])
    result = runner.invoke(main, ["check", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "all decisions fresh" in result.output


def test_check_flags_stale_with_exit_code(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )

    # Mutate the anchored file so its content_hash no longer matches
    anchored = tmp_path / "src" / "payment.ts"
    anchored.write_text("// completely different content\n", encoding="utf-8")

    result = runner.invoke(main, ["check", "--path", str(tmp_path)])
    # Exit 1 when staleness is detected — useful in CI
    assert result.exit_code == 1
    assert "stale" in result.output.lower() or "missing" in result.output.lower()


def test_check_json_output(
    runner: CliRunner,
    tmp_path: Path,
    transcript_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATIONALE_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner.invoke(
        main,
        [
            "capture",
            "--path",
            str(tmp_path),
            "--transcript",
            str(transcript_file),
        ],
    )
    result = runner.invoke(
        main, ["check", "--json", "--all", "--path", str(tmp_path)]
    )
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    if parsed:
        entry = parsed[0]
        assert "status" in entry
        assert "anchors" in entry

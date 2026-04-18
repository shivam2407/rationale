"""Rationale CLI: init, capture, why, list, install-hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from rationale import __version__
from rationale.capture import current_git_sha, parse_transcript
from rationale.distiller import Distiller
from rationale.query import QueryHit, query
from rationale.storage import DecisionStore


def _store(path: str | None) -> DecisionStore:
    root = Path(path) if path else Path.cwd()
    return DecisionStore(root)


@click.group(help="Rationale — git blame for AI decisions.")
@click.version_option(__version__, prog_name="rationale")
def main() -> None:
    pass


@main.command(help="Initialize .rationale/ in the current repo.")
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
def init(path: str | None) -> None:
    store = _store(path)
    target = store.init()
    click.echo(f"initialized: {target}")


@main.command(
    "capture",
    help=(
        "Distill a Claude Code session transcript and save decisions. "
        "Reads stop-hook JSON from stdin if no --transcript is given."
    ),
)
@click.option("--transcript", "transcript", default=None, help="Path to JSONL.")
@click.option(
    "--path",
    default=None,
    help="Repo root. Defaults to the Stop-hook `cwd` field, then Path.cwd().",
)
@click.option("--quiet", is_flag=True, help="Suppress non-error output.")
def capture_cmd(transcript: str | None, path: str | None, quiet: bool) -> None:
    stdin_payload = _read_stdin_payload()
    transcript_path = transcript or (stdin_payload or {}).get("transcript_path")
    if not transcript_path:
        click.echo(
            "rationale: no transcript. Pass --transcript or pipe Stop-hook JSON "
            "with a 'transcript_path' field on stdin.",
            err=True,
        )
        # Exit 1 is non-blocking for Claude Code's Stop hook (exit 2 would
        # tell the agent to RESUME instead of stopping, creating a loop).
        sys.exit(1)

    # Resolution order: explicit --path > Stop-hook cwd > CLAUDE_PROJECT_DIR
    # > Path.cwd(). This lets a single global hook in ~/.claude/settings.json
    # serve every repo the user works in.
    resolved_path = (
        path
        or (stdin_payload or {}).get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
    )
    store = _store(resolved_path)
    store.init()
    trace = parse_transcript(transcript_path)
    distiller = Distiller(git_sha=current_git_sha(store.root))
    decisions = distiller.distill(trace)

    saved: list[Path] = []
    for d in decisions:
        saved.append(store.save(d))

    if not quiet:
        click.echo(f"captured {len(saved)} decision(s) from {transcript_path}")
        for p in saved:
            try:
                click.echo(f"  {p.relative_to(store.root)}")
            except ValueError:
                click.echo(f"  {p}")


@main.command("why", help='Lookup decisions: `why src/x.py:42` or `why "retry"`.')
@click.argument("term")
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of human output.",
)
def why_cmd(term: str, path: str | None, as_json: bool) -> None:
    store = _store(path)
    hits = query(store, term)
    if as_json:
        click.echo(json.dumps([_hit_to_dict(h) for h in hits], indent=2))
        return
    if not hits:
        click.echo(f"no decisions match: {term}")
        return
    for h in hits:
        _print_hit(h)


@main.command("list", help="List all captured decisions.")
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
def list_cmd(path: str | None) -> None:
    store = _store(path)
    decisions = store.all()
    if not decisions:
        click.echo("no decisions captured yet")
        return
    for d in sorted(decisions, key=lambda x: x.timestamp, reverse=True):
        files = ", ".join(d.files) or "—"
        click.echo(f"{d.id}  {d.timestamp}  {d.chosen}  [{files}]")


@main.command(
    "install-hook",
    help="Print (or copy) the Claude Code Stop hook configuration.",
)
@click.option("--path", default=None, help="(unused — kept for forward compat).")
@click.option(
    "--copy",
    is_flag=True,
    help="Copy the snippet to the clipboard (pbcopy/xclip/clip).",
)
@click.option(
    "--bare",
    is_flag=True,
    help="Print only the inner Stop array, for merging into an existing hooks block.",
)
def install_hook_cmd(path: str | None, copy: bool, bare: bool) -> None:
    del path  # reserved; capture resolves cwd from the hook payload
    snippet = build_hook_config()
    if bare:
        snippet_json = json.dumps(snippet["hooks"]["Stop"], indent=2)
        instruction = (
            "Append the following to the existing \"Stop\" array in "
            "~/.claude/settings.json under \"hooks\":"
        )
    else:
        snippet_json = json.dumps(snippet, indent=2)
        instruction = (
            "If ~/.claude/settings.json doesn't exist yet, paste the following "
            "verbatim.\nIf it already has a top-level \"hooks\" object, copy "
            "ONLY the inner\n\"Stop\" array and merge it into your existing "
            "hooks.Stop list (or run `rationale install-hook --bare`):"
        )

    if copy and _copy_to_clipboard(snippet_json):
        click.echo("copied hook config to clipboard.")
        click.echo(instruction)
        return

    click.echo(instruction)
    click.echo(snippet_json)
    if copy:
        click.echo(
            "\n(note: clipboard copy failed — no pbcopy/xclip/clip available)",
            err=True,
        )


def build_hook_config(root: Path | None = None) -> dict:
    # The `root` parameter is kept for forward compatibility but is
    # intentionally unused: the generated command resolves project dir
    # from the Stop-hook `cwd` field and CLAUDE_PROJECT_DIR at runtime.
    del root
    """Return the Claude Code hook config shape for a Stop hook.

    Real Claude Code schema: each Stop entry has a `matcher` plus a nested
    `hooks` list of `{type, command}` objects. This matches the published
    hooks documentation (`docs.anthropic.com/en/docs/claude-code/hooks`).

    The command does NOT hardcode --path: Claude Code spawns hooks with
    the current project's working directory, and `rationale capture`
    also consults the `cwd` field in the hook's stdin JSON. One global
    hook therefore serves every repo the user works in.
    """
    return {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "rationale capture --quiet",
                        }
                    ],
                }
            ]
        }
    }


def _copy_to_clipboard(text: str) -> bool:
    for tool, args in (
        ("pbcopy", ["pbcopy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("wl-copy", ["wl-copy"]),
        ("clip", ["clip"]),  # Windows
    ):
        if shutil.which(tool):
            try:
                proc = subprocess.run(
                    args,
                    input=text,
                    text=True,
                    check=False,
                    timeout=5,
                )
                return proc.returncode == 0
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def why_shortcut() -> None:
    """`why <term>` — wraps `rationale why`. Installed as its own entry point."""
    if len(sys.argv) < 2:
        click.echo("usage: why <file>:<line>  |  why \"<term>\"", err=True)
        sys.exit(2)
    # Pass through to click's normal entry path so exit codes, --help,
    # and error handling behave identically to `rationale why ...`.
    sys.argv = ["rationale", "why", *sys.argv[1:]]
    main()


def _read_stdin_payload() -> dict | None:
    """Parse a Stop-hook JSON payload from stdin, if present.

    Returns None when stdin is a TTY, empty, or not a JSON object.
    Prints a stderr warning on invalid JSON so the user isn't left in silence.
    """
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        click.echo(
            "warning: stdin was not valid JSON; ignoring Stop-hook payload",
            err=True,
        )
        return None
    return payload if isinstance(payload, dict) else None


def _hit_to_dict(h: QueryHit) -> dict:
    return {
        "id": h.decision.id,
        "score": h.score,
        "reason": h.reason,
        "chosen": h.decision.chosen,
        "files": h.decision.files,
        "anchors": [a.to_dict() for a in h.decision.anchors],
        "alternatives": h.decision.alternatives,
        "tags": h.decision.tags,
        "confidence": h.decision.confidence,
        "timestamp": h.decision.timestamp,
        "git_sha": h.decision.git_sha,
        "reasoning": h.decision.reasoning,
    }


def _print_hit(h: QueryHit) -> None:
    d = h.decision
    click.echo(f"\n{d.id}  {d.chosen}  ({h.reason})")
    click.echo(f"  when:  {d.timestamp}")
    if d.git_sha:
        click.echo(f"  sha:   {d.git_sha[:10]}")
    for a in d.anchors:
        click.echo(f"  anchor: {a.file}:{a.line_start}-{a.line_end}")
    if d.alternatives:
        click.echo(f"  rejected: {', '.join(d.alternatives)}")
    if d.tags:
        click.echo(f"  tags:  {', '.join(d.tags)}")
    click.echo("")
    for line in d.reasoning.splitlines():
        click.echo(f"  {line}")


if __name__ == "__main__":
    main()

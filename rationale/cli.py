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
from rationale.export import write_export
from rationale.graph import EdgeKind, build_graph
from rationale.query import QueryHit, query
from rationale.rollup import (
    Rollup,
    by_agent,
    by_file,
    by_tag,
    overall_summary,
)
from rationale.staleness import (
    DecisionStaleness,
    Status,
    check_decision,
)
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
    "check",
    help=(
        "Check which decisions have gone stale relative to the current working "
        "tree. Exits 1 if any decisions are STALE or MISSING (useful in CI)."
    ),
)
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of human output.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Also print FRESH/DRIFTED decisions (default: only problems).",
)
def check_cmd(path: str | None, as_json: bool, show_all: bool) -> None:
    store = _store(path)
    decisions = store.all()
    summaries = [check_decision(d, repo_root=store.root) for d in decisions]

    if as_json:
        click.echo(json.dumps([_staleness_to_dict(s) for s in summaries], indent=2))
    else:
        _print_staleness_table(summaries, show_all=show_all)

    if any(s.status in {Status.STALE, Status.MISSING} for s in summaries):
        sys.exit(1)


@main.command(
    "summary",
    help="Confidence-weighted rollups across files, agents, and tags.",
)
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of human output.",
)
@click.option(
    "--top", default=10, type=int, help="Limit to the top N entries per group."
)
def summary_cmd(path: str | None, as_json: bool, top: int) -> None:
    store = _store(path)
    decisions = store.all()
    overall = overall_summary(decisions)
    files = by_file(decisions)[:top]
    agents = by_agent(decisions)[:top]
    tags = by_tag(decisions)[:top]

    if as_json:
        click.echo(
            json.dumps(
                {
                    "total": overall.total,
                    "weighted_score": round(overall.weighted_score, 4),
                    "by_confidence": overall.by_confidence,
                    "by_file": [_rollup_dict(r) for r in files],
                    "by_agent": [_rollup_dict(r) for r in agents],
                    "by_tag": [_rollup_dict(r) for r in tags],
                },
                indent=2,
            )
        )
        return

    if overall.total == 0:
        click.echo("no decisions captured yet")
        return
    click.echo(
        f"total: {overall.total}   "
        f"weighted: {overall.weighted_score:.2f}   "
        f"by confidence: {overall.by_confidence}"
    )
    _print_rollup("by file", files)
    _print_rollup("by agent", agents)
    _print_rollup("by tag", tags)


@main.command(
    "graph",
    help=(
        "Print the decision relationship graph: SUPERSEDES edges (newer "
        "decisions that walked back an older one) and RELATED edges "
        "(overlapping anchors or shared symbols)."
    ),
)
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of human output.",
)
def graph_cmd(path: str | None, as_json: bool) -> None:
    store = _store(path)
    decisions = store.all()
    g = build_graph(decisions)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "nodes": [
                        {"id": n.id, "chosen": n.chosen, "timestamp": n.timestamp}
                        for n in g.nodes
                    ],
                    "edges": [
                        {
                            "source": e.source,
                            "target": e.target,
                            "kind": e.kind.value,
                            "reason": e.reason,
                        }
                        for e in g.edges
                    ],
                },
                indent=2,
            )
        )
        return

    if not g.edges:
        click.echo(f"{len(g.nodes)} decision(s); no relationships yet")
        return
    click.echo(f"{len(g.nodes)} decision(s), {len(g.edges)} edge(s)")
    for e in g.edges:
        arrow = "->" if e.kind == EdgeKind.SUPERSEDES else "<->"
        click.echo(f"  {e.source} {arrow} {e.target}  [{e.kind.value}]  {e.reason}")


@main.command(
    "export",
    help=(
        "Export the decision log as JSON-LD for EU AI Act provenance "
        "disclosure. Use --sign to attach an HMAC-SHA256 proof "
        "(requires RATIONALE_SIGNING_KEY in the environment)."
    ),
)
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path. Defaults to <repo>/.rationale/export.jsonld.",
)
@click.option(
    "--sign",
    is_flag=True,
    help=(
        "Attach an HMAC-SHA256 proof. Requires RATIONALE_SIGNING_KEY "
        "env var. Use --ed25519 for asymmetric Ed25519 instead."
    ),
)
@click.option(
    "--ed25519",
    is_flag=True,
    help=(
        "Use Ed25519 instead of HMAC for signing. Requires the [crypto] "
        "extra and a PEM-encoded private key at RATIONALE_SIGNING_KEY."
    ),
)
def export_cmd(
    path: str | None, output: str | None, sign: bool, ed25519: bool
) -> None:
    store = _store(path)
    target = Path(output) if output else store.base_dir / "export.jsonld"
    try:
        written = write_export(
            store.all(), target, sign=sign, ed25519=ed25519
        )
    except RuntimeError as exc:
        click.echo(f"rationale: export failed: {exc}", err=True)
        sys.exit(1)
    click.echo(f"wrote {written}")


@main.command(
    "mcp",
    help=(
        "Run as an MCP server over stdio — exposes rationale_why / "
        "rationale_list / rationale_check / rationale_summary tools to "
        "MCP-aware agents."
    ),
)
@click.option("--path", default=None, help="Repo root (defaults to cwd).")
def mcp_cmd(path: str | None) -> None:
    from rationale.mcp_server import serve_stdio

    root = Path(path) if path else Path.cwd()
    serve_stdio(root)


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
    """Return the Claude Code hook config shape for a Stop hook.

    Real Claude Code schema: each Stop entry has a `matcher` plus a nested
    `hooks` list of `{type, command}` objects. This matches the published
    hooks documentation (`docs.anthropic.com/en/docs/claude-code/hooks`).

    The command does NOT hardcode --path: Claude Code spawns hooks with
    the current project's working directory, and `rationale capture`
    also consults the `cwd` field in the hook's stdin JSON. One global
    hook therefore serves every repo the user works in.

    The `root` parameter is kept for forward compatibility but is
    intentionally unused: the generated command resolves project dir
    from the Stop-hook `cwd` field and CLAUDE_PROJECT_DIR at runtime.
    """
    del root
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


def _rollup_dict(r: Rollup) -> dict:
    return {
        "key": r.key,
        "count": r.count,
        "weight": round(r.weight, 4),
        "sample_ids": list(r.sample_ids),
    }


def _print_rollup(title: str, rollups: list[Rollup]) -> None:
    if not rollups:
        return
    click.echo(f"\n{title}:")
    for r in rollups:
        samples = ", ".join(r.sample_ids)
        click.echo(
            f"  {r.weight:5.2f}  {r.count:3d}  {r.key}  [{samples}]"
        )


def _staleness_to_dict(s: DecisionStaleness) -> dict:
    return {
        "id": s.decision.id,
        "chosen": s.decision.chosen,
        "status": s.status.value,
        "files": s.decision.files,
        "anchors": [
            {
                "file": r.anchor.file,
                "stored_lines": [r.anchor.line_start, r.anchor.line_end],
                "current_lines": (
                    [r.current_line_start, r.current_line_end]
                    if r.current_line_start is not None
                    else None
                ),
                "status": r.status.value,
                "detail": r.detail,
            }
            for r in s.anchor_reports
        ],
    }


def _print_staleness_table(
    summaries: list[DecisionStaleness], *, show_all: bool
) -> None:
    noisy = {Status.FRESH, Status.DRIFTED, Status.UNKNOWN}
    printed = 0
    for s in summaries:
        if not show_all and s.status in noisy and s.status != Status.UNKNOWN:
            continue
        printed += 1
        marker = _status_marker(s.status)
        files = ", ".join(s.decision.files) or "—"
        click.echo(f"{marker} {s.decision.id}  {s.status.value:<8}  {files}")
        for r in s.anchor_reports:
            if r.status == Status.FRESH and not show_all:
                continue
            line = f"    {r.anchor.file}:{r.anchor.line_start}-{r.anchor.line_end}"
            if r.current_line_start and (
                r.current_line_start != r.anchor.line_start
                or r.current_line_end != r.anchor.line_end
            ):
                line += (
                    f"  →  now {r.current_line_start}-{r.current_line_end}"
                )
            if r.detail:
                line += f"  ({r.detail})"
            click.echo(line)
    if printed == 0:
        click.echo("all decisions fresh")


def _status_marker(status: Status) -> str:
    return {
        Status.FRESH: "[ok]",
        Status.DRIFTED: "[~~]",
        Status.STALE: "[!!]",
        Status.MISSING: "[??]",
        Status.UNKNOWN: "[..]",
    }.get(status, "[..]")


def _print_hit(h: QueryHit) -> None:
    d = h.decision
    click.echo(f"\n{d.id}  {d.chosen}  ({h.reason})")
    click.echo(f"  when:  {d.timestamp}")
    if d.git_sha:
        click.echo(f"  sha:   {d.git_sha[:10]}")
    for a in d.anchors:
        label = f"  anchor: {a.file}:{a.line_start}-{a.line_end}"
        if a.symbol:
            label += f"  [symbol: {a.symbol}]"
        click.echo(label)
    if d.alternatives:
        click.echo(f"  rejected: {', '.join(d.alternatives)}")
    if d.tags:
        click.echo(f"  tags:  {', '.join(d.tags)}")
    click.echo("")
    for line in d.reasoning.splitlines():
        click.echo(f"  {line}")


if __name__ == "__main__":
    main()

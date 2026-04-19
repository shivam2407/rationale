# Rationale

> `git blame` for AI-generated code — code-anchored, repo-local decision log.

AI agents ship code faster than humans can build a mental model of why it exists. Six months later, nobody — not even the person who approved the PR — can explain why a retry was set to 3, why a dependency was added, or why a function was split a particular way. The reasoning dies with the agent session.

**Rationale fixes this.** Every coding session emits a small set of *decisions*: what the agent picked, what it considered, why. They live in your repo, anchored to file + line range, queryable with one command.

```bash
$ why src/payment.ts:42

d-a3f9c1  fixed 3x retry  (exact-line)
  when:  2026-04-16T14:22:00Z
  sha:   1f3b9c2a08
  anchor: src/payment.ts:42-58
  rejected: exponential_backoff, circuit_breaker
  tags:  reliability, payments

  Downstream rate limits already cap traffic, and exponential backoff
  would stretch p95 past the 800ms SLO. Circuit breaker was overkill
  for a single dependency with low failure correlation.
```

## Why this exists

| Existing tool | What it captures | What it misses |
|---|---|---|
| LangSmith / Langfuse / AgentOps | Spans, latency, tool calls in a SaaS dashboard | Not anchored to code. Lives outside the repo. |
| `claude-trace`, session logs | Per-session transcripts | Ephemeral. Not queryable across history. |
| ADR tools (adr-tools, Workik) | High-level architectural decisions | Manual. Disconnected from specific lines. |
| Agent memory (mem0, claude-mem) | Context for the *next* session | Optimized for the agent, not for a human auditing 6 months later. |
| AI Provenance Protocol | Who/when/which-model authored code | Captures authorship, not intent. |

**The gap:** nobody produces a *code-anchored, queryable, repo-local* record of agent reasoning that survives across sessions. Rationale is that record.

## Install

```bash
pip install rationale-cli

# Optional: enable Haiku-based distillation (otherwise the heuristic
# offline distiller runs and no API calls are made).
pip install "rationale-cli[llm]"
```

> The distribution on PyPI is `rationale-cli` because the `rationale`
> name was already taken by an unrelated project. The Python module,
> the CLI binary, the MCP server, and the Claude Code plugin all keep
> the name `rationale`.

Or from source:

```bash
git clone https://github.com/shivam2407/rationale.git
cd rationale
pip install -e .
```

Requires Python 3.10+.

### Install as a Claude Code plugin

If you use Claude Code, install rationale as a plugin and the Stop hook,
MCP server, and slash commands are wired up for you:

```bash
# Inside Claude Code
/plugin marketplace add shivam2407/rationale
/plugin install rationale@rationale
```

You still need `pip install rationale-cli` so the CLI is on `PATH`, but after
that the plugin provides:

- `/why`, `/rationale-check`, `/rationale-summary`, `/rationale-graph`,
  `/rationale-export` slash commands
- `hooks/hooks.json` — the Stop hook that captures each session
- `.mcp.json` — registers the `rationale` MCP server so `rationale_why`
  / `rationale_list` / `rationale_check` / `rationale_summary` are
  callable by agents during the session

## Quickstart

```bash
# 1. Initialize the decision log in your repo
cd your-project
rationale init

# 2. Wire up your agent (see the next section for Claude Code, Copilot
#    CLI, and others).

# 3. Use the agent normally. Decisions get captured at session end.

# 4. Six months later:
why src/payment.ts:42
why "retry"
rationale list

# 5. Before a release, check which decisions have gone stale
rationale check
```

## Wiring up your coding agent

### Claude Code (native)

```bash
rationale install-hook          # print the Stop-hook JSON
rationale install-hook --copy   # print + try clipboard
rationale install-hook --bare   # only the inner `Stop` array, easy to merge
```

Add the snippet to `~/.claude/settings.json` under the top-level `"hooks"`
key. The snippet matches Claude Code's real Stop-hook schema
(`{matcher, hooks: [{type: "command", command: "rationale capture --quiet"}]}`),
and the command intentionally does **not** hardcode a repo path — Claude
Code spawns the hook with the project's working directory and also passes
`cwd` in the Stop-hook JSON, so one global hook serves every repo you
work in.

A complete example for an empty `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {"type": "command", "command": "rationale capture --quiet"}
        ]
      }
    ]
  }
}
```

If you already have a `Stop` array, merge in the new entry rather than
replacing it. Restart Claude Code (or run `/hooks reload`) and decisions
will start landing in `.rationale/` after each session.

### GitHub Copilot CLI / Copilot Coding Agent

Copilot CLI does not currently expose a session-end hook, so v0 captures
Copilot sessions by handing the transcript to `rationale capture` after
the fact:

```bash
# After a Copilot CLI session, point rationale at the transcript file.
# Copilot CLI logs live under ~/.copilot/logs/; the exact path may vary
# by version — check `gh copilot --help` or the Copilot CLI docs.
rationale capture --transcript ~/.copilot/logs/<session>.jsonl

# Or pipe a Stop-hook-style JSON payload via stdin:
echo '{"transcript_path":"/path/to/session.jsonl","cwd":"'"$PWD"'"}' \
  | rationale capture --quiet
```

Wrap this in a shell alias or a git pre-commit hook to make it automatic.
First-class Copilot integration (a daemon that watches the log directory)
is on the v1 roadmap — contributions welcome.

### Other agents (Cursor, Codex CLI, Aider, MCP-aware tools)

Any agent that produces a JSON or JSONL session transcript can feed
`rationale capture`:

```bash
rationale capture --transcript path/to/session.jsonl --path "$PWD"
```

Rationale's transcript parser already accepts the common shapes:
`{role: assistant, content: [{type: thinking|text|tool_use, ...}]}`.
For agents with their own format, write a tiny adapter that converts to
Claude Code's transcript shape and pipe it in via stdin.

## How it works

```
Claude Code session
        │
        ▼
  Stop hook fires ──→ rationale capture
        │
        ▼
  Parse transcript JSONL  (capture.py)
        │
        ▼
  Distill via Haiku       (distiller.py)
   ↳ extract decision moments only
   ↳ filter mechanical edits
        │
        ▼
  Anchor to file + lines  (anchoring.py)
        │
        ▼
  Save .rationale/2026-04/d-a3f9.md
        │
        ▼
  Query via `why`         (query.py)
```

### Storage format

Every decision is a markdown file with YAML frontmatter:

```markdown
---
id: d-a3f9c1
timestamp: 2026-04-16T14:22:00Z
agent: claude-code
session_id: sess-7b2
git_sha: 1f3b9c2a08...
files: [src/payment.ts]
anchors:
  - file: src/payment.ts
    lines: [42, 58]
alternatives_considered: [exponential_backoff, circuit_breaker]
chosen: fixed 3x retry
confidence: medium
tags: [reliability, payments]
---

Downstream rate limits already cap traffic, and exponential backoff
would stretch p95 past the 800ms SLO. Circuit breaker was overkill
for a single dependency with low failure correlation.
```

This is your repo's permanent record. It is `git`-tracked, plain text, grep-able, and survives any platform migration.

## CLI reference

| Command | What it does |
|---|---|
| `rationale init` | Create `.rationale/` in the repo. |
| `rationale capture --transcript <path>` | Distill one transcript, write decision files. |
| `rationale capture` (stdin) | Reads Stop-hook JSON; used as a hook command. |
| `rationale why <file>:<line>` | Decisions anchored to that line (with drift tolerance). |
| `rationale why <file>` | All decisions touching that file. |
| `rationale why "<term>"` | Text search across decision bodies. |
| `rationale list` | All decisions, newest first. |
| `rationale check` | Report stale decisions vs. the current working tree. Exits 1 when any decision is STALE or MISSING — suitable for CI. |
| `rationale summary` | Confidence-weighted rollup across files, agents, and tags. |
| `rationale graph` | Print the decision relationship graph (`SUPERSEDES`, `RELATED`). |
| `rationale export` | Write an EU AI Act JSON-LD export of the decision log. `--sign` attaches an HMAC-SHA256 proof (requires `RATIONALE_SIGNING_KEY`). |
| `rationale mcp` | Run as an MCP server over stdio so any MCP-aware agent can call `rationale_why`, `rationale_list`, `rationale_check`, `rationale_summary`. |
| `rationale install-hook` | Print the Claude Code Stop-hook config. |

A short `why` shim is also installed so you can type `why src/x.py:42` directly.

Add `--json` to `why` or `check` for machine-readable output (great for
editor integrations).

## Cross-agent integration via MCP

```bash
# Run in your repo. Point any MCP-aware client (Claude Desktop, Cursor,
# agent frameworks) at this command.
rationale mcp
```

The server speaks a JSON-RPC 2.0 subset of the Model Context Protocol
over stdio and exposes five tools:

| Tool | What it does |
|---|---|
| `rationale_record` | **Primary capture path.** Agent calls this the moment it makes a non-trivial choice, passing the alternatives + its own reasoning. Writes directly to `.rationale/`. No distillation LLM, no API key. |
| `rationale_why` | Look up decisions anchored to a file/line or matching a free-text term. |
| `rationale_list` | Every captured decision, newest first. |
| `rationale_check` | Classify each decision FRESH / DRIFTED / STALE / MISSING / UNKNOWN. |
| `rationale_summary` | Confidence-weighted rollups across files, agents, and tags. |

Install the optional `[mcp]` extra if you want to plug into the full
MCP Python SDK:

```bash
pip install "rationale-cli[mcp]"
```

### Why runtime capture is the default in v0.4

Before v0.4, rationale reconstructed decisions *after* a session via the
Stop hook + an LLM distiller. That worked, but had three problems: it
needed an API key (or had to shell out to `claude -p`), it was bounded
by what the agent had explicitly "thought out loud" in its transcript,
and it interpreted that thinking post-hoc rather than recording ground
truth.

Runtime capture flips this. The agent tells you, at the moment of the
decision, what it picked, what it rejected, and why — in its own
words. No interpretation, no extra LLM call, no API key. The Stop hook
remains as a fallback for sessions where the agent didn't use the
tool, and uses the local `claude` binary (Claude Max auth) when available
instead of the Anthropic SDK.

## EU AI Act provenance export

The EU AI Act (August 2026) requires operators of high-risk AI systems
to document the reasoning behind AI-generated artifacts. `rationale`
ships a JSON-LD export with a stable `@context` URL and optional
cryptographic proof:

```bash
# Unsigned export — useful for internal audits
rationale export

# HMAC-SHA256 signature (stdlib only)
export RATIONALE_SIGNING_KEY='a-long-random-string'
rationale export --sign --output audit.jsonld

# Ed25519 asymmetric signing for external auditors
pip install "rationale-cli[crypto]"
export RATIONALE_SIGNING_KEY=/path/to/private.pem
rationale export --sign --ed25519 --output audit.jsonld
```

Signatures cover canonical JSON (sorted keys, compact separators) so
verification is reproducible.

## Surviving refactors

Every anchor captured in v1 records two extra fields beyond the line range:

- a **symbol path** (e.g. `PaymentService.retry`) extracted from the
  enclosing function/class, and
- a **content fingerprint** (SHA-256 of the anchored block, normalized
  for trailing whitespace).

Together they let `rationale check` classify a decision into one of five
states:

| Status | Meaning |
|---|---|
| FRESH | Content at the stored line range still matches. |
| DRIFTED | Content matches, but the symbol moved to a new line range. Still valid — the anchor quietly re-binds on next capture. |
| STALE | Symbol still exists but the body changed. The rationale may no longer apply; review it. |
| MISSING | File or symbol is gone. |
| UNKNOWN | Anchor predates v1 and has no content hash; can't classify. |

Drop `rationale check` into CI to fail the build when a PR touches code
with stale reasoning and doesn't update it.

## Configuration

| Env var | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Enables LLM-based distillation via Haiku. |
| `RATIONALE_OFFLINE=1` | Force the heuristic offline distiller (no API calls). |

In offline mode, Rationale produces a low-confidence decision per edited file, drawn from the agent's thinking text. Useful in CI and air-gapped environments.

## Roadmap

- **v0** — Claude Code hook, Haiku distiller, line-range anchors, `why` CLI.
- **v1** — Symbolic anchors (Python `ast` + regex for JS/TS/Go/Rust), content-hash staleness detector, `rationale check` with CI exit code.
- **v2 (this release)** — Cross-agent MCP server, EU AI Act JSON-LD export with HMAC/Ed25519 signing, decision graph (`SUPERSEDES`/`RELATED`), confidence-weighted rollups.
- **post-v2** — Tree-sitter backend for broader language coverage, VS Code CodeLens extension, Cursor extension, team sync server, semantic contradiction detection via embeddings.

See [`rationale-architecture.md`](rationale-architecture.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for release notes.

## Contributing

This is open source under MIT. The `.rationale/` format is a spec, not a product lock-in. Pull requests welcome — please add tests for any new behavior.

```bash
pip install -e .[dev]
pytest
```

## License

MIT — see [LICENSE](LICENSE).

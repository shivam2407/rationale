# Rationale — Decision Log for AI-Generated Code

## The Problem

AI agents ship code faster than humans can build a mental model of why the code exists. Six months later, nobody — not even the person who approved the PR — can explain why a retry policy was set to 3, why a dependency was added, or why a function was split a certain way. The reasoning dies with the agent session.

## Competitive Landscape (15 tools surveyed)

| Category | Tools | What they do | What they miss |
|---|---|---|---|
| **LLM observability** | LangSmith, Langfuse, AgentOps, Maxim, Arize, Braintrust, Comet Opik, LangWatch, Alyx | Ops-level tracing: spans, latency, tool calls, token usage | Not code-anchored. Lives in SaaS dashboards, not the repo. Answers "what happened" not "why this design choice" |
| **Claude Code native** | Auto Mode audit trail, claude-trace-viewer, claude-code-log, LangSmith Fetch | Session transcripts, thinking trace export | Ephemeral per-session. Not queryable across history. Proprietary to one agent |
| **ADR tooling** | adr-tools, Workik AI ADR, Equal Experts ADR agents, Shing Lyu's ADR-in-Code | Captures high-level architectural decisions | Manual or ad-hoc. Disconnected from the specific lines of code. Doesn't capture small-but-important decisions agents make constantly |
| **Agent memory** | mem0, claude-mem, observational memory, Spring AutoMemoryTools | Preserves context for the *next* session | Optimized for agent continuity, not for a human auditing code 6 months later |
| **Code provenance** | AI Provenance Protocol, Beyond Identity, CodeBrewTools | Tracks *which model* wrote the code, cryptographic chain-of-custody | Captures who/when/what, never *why* |

**The gap all 15 share:** Nobody produces a **code-anchored, queryable, repo-local record of agent reasoning** that survives across sessions and works across agents. Observability tools live in dashboards. ADRs are manual. Memory tools are next-session. Provenance tools track authorship, not intent.

## The Insight

> Decisions belong next to the code they affect, not in a SaaS dashboard.

Like `CODEOWNERS` or `.gitignore`, a decision log should live in the repo, be grep-able, survive a platform migration, and travel with the code. The unit isn't the *session* — it's the *decision*, anchored to a line range.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    CAPTURE LAYER                          │
│  Agent adapters (Claude Code hook, Cursor ext,            │
│  Codex plugin, Aider wrapper, MCP server)                 │
│  → emit raw session events + thinking traces              │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                 DISTILLATION LAYER                        │
│  LLM pipeline extracts decision events:                   │
│  { context, alternatives, chosen, reasoning,              │
│    confidence, files_touched, line_ranges }               │
│  Filters noise — keeps moments where agent *chose*        │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                  ANCHORING LAYER                          │
│  Links each decision to git SHA + file + AST node         │
│  Survives refactors via semantic (not line-number) anchor │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│                   STORAGE LAYER                           │
│  Local-first: .rationale/ directory in repo               │
│  Markdown files with YAML frontmatter, git-tracked        │
│  Optional team sync: signed events → central viewer       │
└──────────────────────────┬───────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ┌─────────┐   ┌──────────┐   ┌──────────────┐
      │  QUERY  │   │STALENESS │   │  EXPORT      │
      │   CLI   │   │ DETECTOR │   │  (EU AI Act, │
      │ VS Code │   │          │   │   audits)    │
      └─────────┘   └──────────┘   └──────────────┘
```

### Layer details

**1. Capture (cross-agent)**
- Claude Code: Stop hook + thinking-trace export (OTEL beta)
- Cursor: IDE extension intercepting agent responses
- Codex CLI, Aider: process wrappers
- MCP server: standard endpoint any agent can emit to
- Open schema (v1): `DecisionEvent` with session ID, agent, timestamp, prompt, thinking excerpt, tool calls, diff

**2. Distillation**
- Input: raw trace (thousands of tokens per session)
- Pipeline: classifier flags *choice moments* (branches in thinking, rejected alternatives), then extractor produces a structured decision
- Output: ~5-20 decisions per session, not thousands of spans
- Runs locally via small model (Haiku-class) to keep cost low

**3. Anchoring**
- Each decision links to file paths + AST node IDs (tree-sitter)
- AST-based anchors survive refactors better than line numbers
- Git commit SHA recorded; diff stored for context

**4. Storage**
- `.rationale/YYYY-MM/decision-<hash>.md` with frontmatter:
  ```yaml
  id: d-a3f9
  timestamp: 2026-04-16T14:22:00Z
  agent: claude-code
  files: [src/payment.ts]
  anchors: [{ast_id: PaymentService.retry, lines: [42, 58]}]
  alternatives_considered: [exponential_backoff, fixed_3x, circuit_breaker]
  chosen: fixed_3x
  confidence: medium
  tags: [reliability, payments]
  ```
  Body: human-readable reasoning paragraph.
- Git-tracked by default. Repo travels, rationale travels.

**5. Query interface**
- `why src/payment.ts:42` — returns decision(s) anchored to that line
- `why "retry"` — semantic search across decisions
- VS Code CodeLens: inline "📝 Decision #d-a3f9" above functions with linked rationale
- `why-diff HEAD~10` — what decisions landed in the last 10 commits

**6. Staleness detector**
- When anchored code is modified, flag the decision as potentially stale
- Optional: auto-prompt the agent on next session: "this decision was about X, does current code still match?"
- Prevents rationale drift

**7. Export**
- EU AI Act (Aug 2026) requires provenance disclosure. Rationale ships compliance export out of the box.
- JSON-LD format with cryptographic signing for audit trails

## Why this beats each category

| They have | We have |
|---|---|
| Session traces in a dashboard | Decisions in the repo, next to the code |
| Manual ADRs for big choices | Auto-captured decisions for every meaningful choice |
| Next-session memory | Permanent historical record, queryable forever |
| Who/when provenance | Who/when/**why** provenance |
| Per-agent proprietary logs | One log across all coding agents |

## v0 Scope (ship in a weekend)

1. Claude Code Stop hook → capture session JSONL
2. Haiku-based distiller → extract 5-20 decisions
3. Write to `.rationale/` with git SHA + file anchors (line numbers only, no AST yet)
4. `why <file>:<line>` CLI — simple grep-plus-semantic-search
5. Demo video: "here's a function from 3 months ago — `why`, and I get the reasoning back"

## v1 (month 2)

- Cursor extension
- AST-based anchoring via tree-sitter
- VS Code CodeLens
- Staleness detector
- Team sync server (optional)

## v2

- Cross-agent MCP server
- EU AI Act export
- Decision graph (which decisions contradicted prior ones)
- Confidence-weighted rollups for tech leads

## Distribution strategy

- Open source on day 1 (MIT). `.rationale/` format as a spec, not a product lock-in.
- Medium article as launch: "I built the `git blame` for AI decisions — here's why your repo already needs it"
- HN / Show HN post anchored on the gap analysis
- Target Claude Code power users first (smallest beachhead, strongest pain)
- Paid tier later: team dashboard, semantic search, compliance export

## Open questions / risks

- **Cost of distillation:** running Haiku on every session adds latency + $. Needs caching and batching.
- **Noise vs. signal:** filtering which choices deserve a decision record is the hard ML problem. v0 can be lossy.
- **Agent cooperation:** works best if agents emit structured thinking. Works via trace parsing otherwise.
- **Refactor survival:** AST anchors help but break under large rewrites. Need fuzzy re-anchoring.

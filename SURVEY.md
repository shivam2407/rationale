# Tool Survey: Why None of the 15+ Existing Tools Solve This

Before building Rationale, we surveyed existing tools across five categories to understand what the space already covers — and where the gap is.

**The gap all categories share:** nobody produces a code-anchored, queryable, repo-local record of agent reasoning that survives across sessions and works across agents.

---

## Category 1: LLM Observability (9 tools)

These tools answer *what happened* operationally — spans, latency, token usage, tool calls. They live in SaaS dashboards, not your repo. They never capture *why a specific design choice was made*.

| Tool | What it does | What it misses |
|------|-------------|----------------|
| [LangSmith](https://smith.langchain.com) | Full LLM tracing, evals, dataset management | Not code-anchored; answers operational "what", not design "why" |
| [Langfuse](https://langfuse.com) | Open-source LLM observability, traces & scores | Same — ops-level, no connection to repo or line ranges |
| [AgentOps](https://agentops.ai) | Agent session recording, replay, cost tracking | Session-scoped; no persistent queryable history across sessions |
| [Maxim AI](https://getmaxim.ai) | LLM evals and guardrails | Evaluation-focused, not decision-capture |
| [Arize AI](https://arize.com) | ML observability and LLM tracing | Enterprise MLOps; no code-anchor layer |
| [Braintrust](https://braintrust.dev) | LLM evals and logging | Eval-centric; reasoning not surfaced as repo artifact |
| [Comet Opik](https://comet.ml/opik) | Open-source LLM tracing and evals | Similar to Langfuse; no repo integration |
| [LangWatch](https://langwatch.ai) | LLM monitoring and analytics | Dashboard-only; no line-range anchoring |
| [Alyx](https://alyx.ai) | Agent workflow tracing | Proprietary; no cross-agent standard |

---

## Category 2: Claude Code Native Tooling (4 tools)

These are the closest to the problem — they capture Claude Code session data. But they are ephemeral per-session, not queryable across history, and proprietary to one agent.

| Tool | What it does | What it misses |
|------|-------------|----------------|
| Auto Mode audit trail | Built-in Claude Code session JSONL export | Ephemeral; not queryable; no distillation into decisions |
| [claude-trace-viewer](https://github.com/nickvdyck/claude-trace) | Visualizes Claude Code session traces | Single-session; no persistent store; no code anchoring |
| claude-code-log | Community tool for exporting session logs | Raw logs only; no structured decision extraction |
| LangSmith Claude Fetch | Pipes Claude traces into LangSmith | Inherits LangSmith's dashboard-only limitation |

---

## Category 3: ADR Tooling (4 tools)

Architecture Decision Records capture *big* choices manually. They're disconnected from specific lines of code and require human effort to maintain. They don't capture the 50 small-but-important choices an agent makes in a single session.

| Tool | What it does | What it misses |
|------|-------------|----------------|
| [adr-tools](https://github.com/npryce/adr-tools) | CLI for creating and managing ADR markdown files | Fully manual; no agent integration; no code anchoring |
| [Workik AI ADR](https://workik.com) | AI-assisted ADR generation | Still manual/prompted; coarse-grained; not auto-captured |
| Equal Experts ADR agents | Agent-assisted ADR workflow | Requires explicit invocation; not continuous capture |
| [Shing Lyu's ADR-in-Code](https://shinglyu.com/web/2019/10/22/architecture-decision-records.html) | ADRs embedded as code comments | Manual; no structured frontmatter; not queryable |

---

## Category 4: Agent Memory Tools (4 tools)

These preserve context for the *next* agent session. That's a different problem. Memory tools optimize for agent continuity, not for a human auditing code six months later.

| Tool | What it does | What it misses |
|------|-------------|----------------|
| [mem0](https://mem0.ai) | Persistent memory layer for AI agents | Optimized for agent recall, not human audit; no code anchoring |
| [claude-mem](https://github.com/anthropics/claude-code) | Claude Code's built-in CLAUDE.md memory | Session-priming only; not a historical decision record |
| Observational memory patterns | Auto-summarize sessions into memory files | Agent-centric continuity; not line-anchored or queryable by humans |
| Spring AutoMemoryTools | Auto memory management for agent frameworks | Framework-specific; no repo-local storage |

---

## Category 5: Code Provenance Tools (3 tools)

These track *who* wrote code and *when*, with cryptographic chain-of-custody. Useful for compliance. They never capture *why* a design choice was made.

| Tool | What it does | What it misses |
|------|-------------|----------------|
| [AI Provenance Protocol / C2PA](https://c2pa.org) | Cryptographic watermarking of AI-generated content | Who/when/what only; no reasoning capture |
| [Beyond Identity](https://beyondidentity.com) | Developer identity and code signing | Identity layer; no decision reasoning |
| CodeBrewTools | AI code attribution tracking | Tracks authorship, not intent |

---

## Summary

| Category | Tools Surveyed | Closest to solving it | Fatal gap |
|----------|---------------|----------------------|-----------|
| LLM Observability | 9 | LangSmith | Dashboard-only, not repo-local |
| Claude Code Native | 4 | Auto Mode audit trail | Ephemeral, not distilled or queryable |
| ADR Tooling | 4 | adr-tools | Manual, not auto-captured, not line-anchored |
| Agent Memory | 4 | mem0 | Agent-centric, not human-audit-centric |
| Code Provenance | 3 | AI Provenance Protocol | Who/when, never why |

**None of the 24 tools surveyed produce a code-anchored, queryable, repo-local record of agent reasoning that survives across sessions and works across agents.**

That's the gap Rationale fills.

---

*Survey conducted April 2026. Tool capabilities reflect public documentation at time of research. PRs welcome if something is inaccurate or a tool has shipped this feature since.*

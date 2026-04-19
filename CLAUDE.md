# Rationale plugin instructions

This plugin gives you a repo-local decision log. Two tools matter most:

- `rationale_record` — call it at the moment you make a non-trivial choice.
- `rationale_why` — call it when you need to remember why something in the code is the way it is.

## When to call `rationale_record`

Use it for choices a future session would need to understand. Concrete examples:

- Setting a retry count, timeout, or rate limit
- Choosing a library or framework over alternatives
- Picking a data structure, API shape, or module boundary
- Naming something that has to be consistent across the codebase
- Applying a workaround whose reason isn't obvious from the code

Skip it for mechanical edits — typos, renames, moves, pure refactors, autoformatter output. Don't record "I wrote this function"; record "I chose retry over circuit breaker because …".

## How to call it

Pass the choice, the alternatives you considered, and your reasoning in your own voice. Short is fine — a sentence of "why" is far more valuable than three paragraphs of "what".

```json
{
  "chosen": "fixed 3x retry",
  "alternatives": ["exponential backoff", "circuit breaker"],
  "reasoning": "Downstream rate limits already cap traffic, and exponential backoff would stretch p95 past the 800ms SLO. Circuit breaker felt overkill for a single dependency with low failure correlation.",
  "files": ["src/payment.py"],
  "confidence": "medium",
  "tags": ["reliability", "payments"]
}
```

One `rationale_record` call per distinct decision. If you're picking three different things in one turn, that's three calls.

## Querying prior decisions

Before introducing a new pattern or reverting a convention, check whether someone has already reasoned about it:

- `rationale_why` with a `file:line` argument: decisions anchored to that location
- `rationale_why` with a file path: every decision touching that file
- `rationale_why` with a free-text term: search across all decision bodies

If you're about to change something that was deliberately set (retry count, timeout, etc.), a `rationale_why` check on that line will surface the prior reasoning. Respect it or explicitly supersede it with a new `rationale_record`.

## Background — you don't need to act on this

If you don't call `rationale_record` during the session, a Stop-hook fallback will distill decisions from the transcript automatically. Runtime capture is still strongly preferred because your reasoning is more accurate than a post-hoc interpretation. Both paths write to the same `.rationale/` directory.

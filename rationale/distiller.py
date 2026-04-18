"""Distillation layer: turn raw session traces into structured Decisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from rationale.capture import SessionTrace
from rationale.models import Decision, DecisionAnchor
from rationale.symbols import hash_file_range, symbol_at_line

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_DECISIONS = 20
MIN_DECISIONS = 0  # sessions can legitimately have zero structural decisions


SYSTEM_PROMPT = """You extract decision moments from an AI coding agent's session.

A "decision" is a moment where the agent picked one approach over alternatives.
Examples: choosing a retry count, picking a library, splitting a function,
adding a dependency, naming a module, structuring an API contract.

NOT decisions: tool calls without alternatives, mechanical edits, explanations.

For each decision, output a JSON object with these fields:
- "chosen": short noun phrase of what was picked (e.g. "fixed 3x retry")
- "alternatives": list of 0-4 alternatives the agent considered or rejected
- "reasoning": one paragraph explaining the why (drawn from the agent's own thinking)
- "files": list of file paths the decision affects (use only paths from the trace)
- "confidence": "low" | "medium" | "high" — how confident the agent seemed
- "tags": 1-4 short tags (e.g. ["reliability", "payments"])

Output ONLY a JSON array of 0 to 20 objects, no prose, no fences.
If the session contains no real decisions, output [].
"""


class LLMClient(Protocol):
    def complete(self, system: str, user: str, model: str) -> str: ...


@dataclass
class AnthropicClient:
    """Wraps the Anthropic Messages API. Lazy-imports the SDK so the package
    stays functional when installed without the ``[llm]`` extra."""

    api_key: str | None = None

    def complete(self, system: str, user: str, model: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK is not installed. Install `rationale[llm]` "
                "to enable LLM-based distillation, or set RATIONALE_OFFLINE=1."
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        chunks: list[str] = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        return "".join(chunks)


class Distiller:
    """Turns a SessionTrace into a list of Decision objects.

    The LLM call is injected so tests and offline modes can swap it out.
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        model: str = DEFAULT_MODEL,
        git_sha: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.git_sha = git_sha

    def distill(self, trace: SessionTrace) -> list[Decision]:
        if not _has_signal(trace):
            return []
        client = self._resolve_client()
        if client is None:
            return _heuristic_distill(trace, self.git_sha)
        try:
            raw = client.complete(
                system=SYSTEM_PROMPT,
                user=trace.to_prompt_payload(),
                model=self.model,
            )
        except Exception as exc:  # pragma: no cover - runtime-only paths
            # Network failure, 429, bad key, timeout, SDK missing at call
            # time — we never want to crash the user's Stop hook. Fall back
            # to the heuristic distiller and let the user know.
            import sys as _sys

            print(
                f"rationale: warning: LLM distillation failed ({type(exc).__name__}: "
                f"{exc}); falling back to heuristic mode.",
                file=_sys.stderr,
            )
            return _heuristic_distill(trace, self.git_sha)

        items = _parse_json_array(raw)
        decisions: list[Decision] = []
        for idx, item in enumerate(items[:MAX_DECISIONS]):
            d = _build_decision(item, trace, self.git_sha, idx)
            if d:
                decisions.append(d)
        # If the LLM returned no usable decisions but the session had signal,
        # fall back to the heuristic so the user gets *something* captured.
        if not decisions:
            return _heuristic_distill(trace, self.git_sha)
        return decisions

    def _resolve_client(self) -> LLMClient | None:
        if self.client is not None:
            return self.client
        if os.environ.get("RATIONALE_OFFLINE") == "1":
            return None
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        # Probe for the optional SDK up front so offline-only installs never
        # hit a late-stage RuntimeError inside the Stop hook.
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return None
        return AnthropicClient()


def _has_signal(trace: SessionTrace) -> bool:
    return bool(trace.thinking or trace.assistant_text or trace.edits)


def _parse_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _build_decision(
    item: dict,
    trace: SessionTrace,
    git_sha: str | None,
    index: int,
) -> Decision | None:
    chosen = str(item.get("chosen", "")).strip()
    reasoning = str(item.get("reasoning", "")).strip()
    if not chosen or not reasoning:
        return None

    confidence = str(item.get("confidence", "medium")).strip().lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    files = [str(f) for f in item.get("files", []) if isinstance(f, str)]
    anchors = _resolve_anchors(files, trace)

    tags = [str(t) for t in item.get("tags", []) if isinstance(t, str)][:4]
    alternatives = [
        str(a) for a in item.get("alternatives", []) if isinstance(a, str)
    ][:6]

    decision_id = _decision_id(trace.session_id, index, chosen)
    return Decision(
        id=decision_id,
        timestamp=_now_iso(),
        agent=trace.agent,
        chosen=chosen,
        reasoning=reasoning,
        anchors=anchors,
        alternatives=alternatives,
        confidence=confidence,
        tags=tags,
        git_sha=git_sha,
        session_id=trace.session_id,
    )


def _resolve_anchors(files: list[str], trace: SessionTrace) -> list[DecisionAnchor]:
    anchors: list[DecisionAnchor] = []
    seen: set[tuple[str, int, int]] = set()
    edits_by_file: dict[str, list] = {}
    for e in trace.edits:
        edits_by_file.setdefault(e.file, []).append(e)

    for f in files:
        matched = edits_by_file.get(f) or _fuzzy_lookup(f, edits_by_file)
        if matched:
            line_start = min(e.line_start for e in matched)
            line_end = max(e.line_end for e in matched)
            file_path = matched[0].file
            key = (file_path, line_start, line_end)
            if key not in seen:
                seen.add(key)
                anchors.append(_enriched_anchor(file_path, line_start, line_end))
        else:
            key = (f, 1, 1)
            if key not in seen:
                seen.add(key)
                anchors.append(_enriched_anchor(f, 1, 1))
    return anchors


def _enriched_anchor(file: str, line_start: int, line_end: int) -> DecisionAnchor:
    """Attach symbol + content hash to an anchor when the file is readable.

    Symbol and hash survive refactors better than line numbers:
    - symbol lets us re-locate the anchor when the block moves
    - content_hash lets us detect that the code itself has changed
    If the file isn't on disk (typical during tests where edits reference
    synthetic paths), both fields stay None and the anchor degrades to v0.
    """
    sym = symbol_at_line(file, line_start)
    symbol_name = sym.name if sym else None
    digest = hash_file_range(file, line_start, line_end)
    return DecisionAnchor(
        file=file,
        line_start=line_start,
        line_end=line_end,
        symbol=symbol_name,
        content_hash=digest,
    )


def _fuzzy_lookup(file: str, edits_by_file: dict[str, list]) -> list:
    """Find edits for `file` when the LLM used a shortened path.

    Matches only on path-component-aligned suffixes so 'payment.ts'
    doesn't spuriously match 'cache_payment.ts'.
    """
    norm_file = file.replace("\\", "/").lstrip("/")
    for k, v in edits_by_file.items():
        norm_k = k.replace("\\", "/").lstrip("/")
        if norm_k == norm_file:
            return v
        if _segment_suffix(norm_k, norm_file) or _segment_suffix(norm_file, norm_k):
            return v
    return []


def _segment_suffix(short: str, long: str) -> bool:
    if not short or not long or len(short) >= len(long):
        return False
    if not long.endswith(short):
        return False
    return long[-len(short) - 1] == "/"


def _decision_id(session_id: str, index: int, chosen: str) -> str:
    payload = f"{session_id}|{index}|{chosen}".encode("utf-8")
    h = hashlib.sha1(payload).hexdigest()[:6]
    return f"d-{h}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _heuristic_distill(
    trace: SessionTrace, git_sha: str | None
) -> list[Decision]:
    """Offline fallback: extract one decision per edited file from thinking text.

    This keeps the tool useful in CI, tests, and air-gapped environments.
    The output is intentionally lossy compared to the LLM path.
    """
    if not trace.edits:
        return []
    reasoning_pool = "\n".join(trace.thinking or trace.assistant_text or [])
    reasoning = reasoning_pool.strip() or "No reasoning trace captured."
    decisions: list[Decision] = []
    seen_files: set[str] = set()
    for idx, edit in enumerate(trace.edits):
        if edit.file in seen_files:
            continue
        seen_files.add(edit.file)
        chosen = f"edit {os.path.basename(edit.file)}"
        decisions.append(
            Decision(
                id=_decision_id(trace.session_id, idx, chosen),
                timestamp=_now_iso(),
                agent=trace.agent,
                chosen=chosen,
                reasoning=_truncate(reasoning, 1200),
                anchors=[
                    _enriched_anchor(
                        edit.file, edit.line_start, edit.line_end
                    )
                ],
                alternatives=[],
                confidence="low",
                tags=["heuristic"],
                git_sha=git_sha,
                session_id=trace.session_id,
            )
        )
        if len(decisions) >= MAX_DECISIONS:
            break
    return decisions


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

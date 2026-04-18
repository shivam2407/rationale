"""Data models for decisions and anchors."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DecisionAnchor:
    """Links a decision to a specific code location.

    v1 adds `symbol` (a function/class path) and `content_hash` (SHA-256
    fingerprint of the anchored lines at capture time). Together they let
    the staleness detector (a) re-locate the anchor when lines drift and
    (b) flag the decision when the code itself has changed. Both fields
    are optional — existing v0 decisions without them keep working.
    """

    file: str
    line_start: int
    line_end: int
    ast_id: str | None = None
    symbol: str | None = None
    content_hash: str | None = None

    def contains(self, file: str, line: int) -> bool:
        return self.file == file and self.line_start <= line <= self.line_end

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "file": self.file,
            "lines": [self.line_start, self.line_end],
        }
        if self.ast_id:
            d["ast_id"] = self.ast_id
        if self.symbol:
            d["symbol"] = self.symbol
        if self.content_hash:
            d["content_hash"] = self.content_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionAnchor:
        lines = data.get("lines", [0, 0])
        return cls(
            file=data["file"],
            line_start=int(lines[0]),
            line_end=int(lines[1]),
            ast_id=data.get("ast_id"),
            symbol=data.get("symbol"),
            content_hash=data.get("content_hash"),
        )


@dataclass
class Decision:
    """A captured agent decision: what was chosen, what was rejected, why."""

    id: str
    timestamp: str
    agent: str
    chosen: str
    reasoning: str
    anchors: list[DecisionAnchor] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    confidence: str = "medium"
    tags: list[str] = field(default_factory=list)
    git_sha: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if self.confidence not in {"low", "medium", "high"}:
            raise ValueError(
                f"confidence must be low|medium|high, got: {self.confidence!r}"
            )
        if not self.id:
            raise ValueError("decision id is required")
        if not self.chosen:
            raise ValueError("decision must record what was chosen")
        if not self.reasoning:
            raise ValueError("decision must record reasoning")

    @property
    def files(self) -> list[str]:
        return sorted({a.file for a in self.anchors})

    def to_frontmatter(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "session_id": self.session_id,
            "git_sha": self.git_sha,
            "files": self.files,
            "anchors": [a.to_dict() for a in self.anchors],
            "alternatives_considered": list(self.alternatives),
            "chosen": self.chosen,
            "confidence": self.confidence,
            "tags": list(self.tags),
        }

    @classmethod
    def from_frontmatter(cls, fm: dict[str, Any], body: str) -> Decision:
        anchors = [DecisionAnchor.from_dict(a) for a in fm.get("anchors", [])]
        # YAML parses unquoted ISO 8601 strings as datetime objects; coerce
        # back to str so downstream consumers always see the same type.
        timestamp = fm.get("timestamp") or _now_iso()
        if not isinstance(timestamp, str):
            timestamp = str(timestamp)
        return cls(
            id=str(fm["id"]),
            timestamp=timestamp,
            agent=fm.get("agent", "unknown"),
            chosen=str(fm["chosen"]),
            reasoning=body.strip(),
            anchors=anchors,
            alternatives=list(fm.get("alternatives_considered", [])),
            confidence=fm.get("confidence", "medium"),
            tags=list(fm.get("tags", [])),
            git_sha=fm.get("git_sha"),
            session_id=fm.get("session_id"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

"""Local-first storage for decisions: .rationale/YYYY-MM/d-<hash>.md."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from rationale.models import Decision

RATIONALE_DIR = ".rationale"
# Tolerant of both LF and CRLF line endings so Windows checkouts work.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<fm>.*?)\r?\n---\s*\r?\n?(?P<body>.*)\Z",
    re.DOTALL,
)


class DecisionStore:
    """Repo-local store. One markdown file per decision."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @property
    def base_dir(self) -> Path:
        return self.root / RATIONALE_DIR

    def init(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        readme = self.base_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                "# .rationale/\n\n"
                "Repo-local decision log. Each markdown file captures one "
                "agent decision: what was chosen, what was rejected, and why.\n\n"
                "Query with `rationale why <file>:<line>` or "
                "`rationale why \"<term>\"`.\n",
                encoding="utf-8",
            )
        return self.base_dir

    def save(self, decision: Decision) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        bucket = _bucket_from_timestamp(decision.timestamp)
        bucket_dir = self.base_dir / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        path = bucket_dir / f"{decision.id}.md"
        path.write_text(_render(decision), encoding="utf-8")
        return path

    def load(self, path: Path) -> Decision:
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise ValueError(f"missing YAML frontmatter: {path}")
        fm = yaml.safe_load(match.group("fm")) or {}
        body = match.group("body") or ""
        return Decision.from_frontmatter(fm, body)

    def iter_decisions(
        self, *, warn: bool = True
    ) -> Iterator[tuple[Path, Decision]]:
        if not self.base_dir.exists():
            return
        for path in sorted(self.base_dir.rglob("d-*.md")):
            try:
                yield path, self.load(path)
            except (ValueError, KeyError, yaml.YAMLError) as exc:
                if warn:
                    print(
                        f"rationale: warning: skipping malformed decision "
                        f"{path.relative_to(self.base_dir)}: {exc}",
                        file=sys.stderr,
                    )
                continue

    def all(self) -> list[Decision]:
        return [d for _, d in self.iter_decisions()]


def _render(decision: Decision) -> str:
    fm = yaml.safe_dump(
        decision.to_frontmatter(),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{fm}---\n\n{decision.reasoning.strip()}\n"


def _bucket_from_timestamp(timestamp: str) -> str:
    try:
        dt = datetime.strptime(timestamp[:7], "%Y-%m")
        return dt.strftime("%Y-%m")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m")

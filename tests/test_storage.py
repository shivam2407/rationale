"""Tests for DecisionStore — markdown persistence with YAML frontmatter."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rationale.models import Decision, DecisionAnchor
from rationale.storage import DecisionStore


def _make_decision(**overrides) -> Decision:
    base = dict(
        id="d-aaa111",
        timestamp="2026-04-16T12:00:00Z",
        agent="claude-code",
        chosen="fixed 3x retry",
        reasoning="Downstream rate limits already cap traffic.",
        anchors=[DecisionAnchor("src/payment.ts", 42, 58)],
        alternatives=["exponential_backoff"],
        confidence="medium",
        tags=["reliability"],
        git_sha="deadbeef",
        session_id="sess-1",
    )
    base.update(overrides)
    return Decision(**base)


def test_init_creates_directory_and_readme(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    target = store.init()
    assert target.exists() and target.is_dir()
    assert (target / "README.md").exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.init()
    store.init()
    readme = store.base_dir / "README.md"
    readme.write_text("custom", encoding="utf-8")
    store.init()
    assert readme.read_text(encoding="utf-8") == "custom"


def test_save_writes_under_year_month_bucket(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    decision = _make_decision()
    saved = store.save(decision)
    assert saved.exists()
    assert saved.parent.name == "2026-04"
    assert saved.name == "d-aaa111.md"


def test_save_renders_yaml_frontmatter(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    saved = store.save(_make_decision())
    text = saved.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm_end = text.index("---\n", 4)
    fm = yaml.safe_load(text[4:fm_end])
    assert fm["id"] == "d-aaa111"
    assert fm["chosen"] == "fixed 3x retry"
    assert fm["anchors"][0]["file"] == "src/payment.ts"


def test_load_roundtrips(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    original = _make_decision()
    saved = store.save(original)
    loaded = store.load(saved)
    assert loaded.id == original.id
    assert loaded.chosen == original.chosen
    assert loaded.reasoning == original.reasoning


def test_iter_decisions_returns_all(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.save(_make_decision(id="d-aaa111"))
    store.save(_make_decision(id="d-bbb222", timestamp="2026-05-01T00:00:00Z"))
    results = list(store.iter_decisions())
    assert {d.id for _, d in results} == {"d-aaa111", "d-bbb222"}


def test_load_rejects_file_without_frontmatter(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.init()
    bad = store.base_dir / "2026-04" / "d-bad000.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("no frontmatter here", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(bad)


def test_iter_decisions_skips_malformed_files(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path)
    store.save(_make_decision(id="d-good01"))
    bad = store.base_dir / "2026-04" / "d-bad999.md"
    bad.write_text("---\nnot: valid: yaml: here\n---\nbody", encoding="utf-8")
    results = [d.id for _, d in store.iter_decisions()]
    assert "d-good01" in results


def test_all_returns_empty_when_no_directory(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "nope")
    assert store.all() == []

"""Validate the Claude Code plugin manifests.

The Claude plugin validator is strict and opinionated. A few rules are
enforced that aren't fully documented in the public schema reference,
so we encode them here as tests to keep the plugin installable:

- ``plugin.json`` MUST have ``name``, ``version`` (strings) and MUST NOT
  declare ``hooks`` — ``hooks/hooks.json`` is auto-loaded by convention.
- ``commands``/``agents``/``skills`` fields MUST be arrays (never bare strings).
- Version in ``plugin.json`` + ``marketplace.json`` MUST match the Python
  package's ``__version__`` so the marketplace never advertises a version
  the CLI doesn't ship.
- ``.mcp.json`` MUST register a ``rationale`` server that spawns the
  right CLI entry point.
- Every file under ``commands/`` MUST have a YAML frontmatter block with
  a ``description`` field — Claude Code requires this to list the command.
- ``hooks/hooks.json`` MUST define a Stop hook that shells into
  ``rationale capture``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rationale import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- plugin.json ------------------------------------------------------------


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_exists_and_parses() -> None:
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    assert isinstance(manifest, dict)
    assert manifest["name"] == "rationale"


def test_plugin_json_version_matches_package() -> None:
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    assert manifest["version"] == __version__


def test_plugin_json_does_not_declare_hooks() -> None:
    """Schema note: hooks/hooks.json is auto-loaded. Declaring it again
    in plugin.json triggers a "duplicate hooks" install error."""
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    assert "hooks" not in manifest


@pytest.mark.parametrize("field", ["agents", "commands", "skills"])
def test_plugin_json_component_fields_are_arrays_when_present(field: str) -> None:
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    if field in manifest:
        assert isinstance(manifest[field], list), (
            f"{field} must be a list, not {type(manifest[field]).__name__}"
        )


def test_plugin_json_commands_entries_are_strings() -> None:
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    for entry in manifest.get("commands", []):
        assert isinstance(entry, str)


def test_plugin_json_declares_required_metadata() -> None:
    manifest = _load(REPO_ROOT / ".claude-plugin" / "plugin.json")
    for required in ("name", "version", "description", "author"):
        assert required in manifest
    assert isinstance(manifest["author"], dict)
    assert "name" in manifest["author"]


# --- marketplace.json -------------------------------------------------------


def test_marketplace_json_exists_and_lists_the_plugin() -> None:
    marketplace = _load(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    assert marketplace["name"] == "rationale"
    assert isinstance(marketplace["plugins"], list)
    names = [p["name"] for p in marketplace["plugins"]]
    assert "rationale" in names


def test_marketplace_plugin_version_matches_package() -> None:
    marketplace = _load(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    for entry in marketplace["plugins"]:
        if entry["name"] == "rationale":
            assert entry["version"] == __version__
            break
    else:  # pragma: no cover - guaranteed by earlier test
        pytest.fail("rationale plugin not found in marketplace.json")


def test_marketplace_plugin_source_is_relative() -> None:
    marketplace = _load(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    entry = next(p for p in marketplace["plugins"] if p["name"] == "rationale")
    # "./" means "this repo" — required when the marketplace and plugin
    # live in the same git repository.
    assert entry["source"].startswith("./") or entry["source"] == "."


# --- .mcp.json --------------------------------------------------------------


def test_mcp_json_registers_rationale_server() -> None:
    mcp = _load(REPO_ROOT / ".mcp.json")
    servers = mcp["mcpServers"]
    assert "rationale" in servers
    entry = servers["rationale"]
    assert entry["command"] == "rationale"
    # The stdio server is invoked via `rationale mcp`
    assert "mcp" in entry["args"]


# --- hooks/hooks.json -------------------------------------------------------


def test_hooks_json_registers_stop_hook() -> None:
    hooks = _load(REPO_ROOT / "hooks" / "hooks.json")
    stop_entries = hooks["hooks"].get("Stop")
    assert isinstance(stop_entries, list)
    assert stop_entries, "Stop hook must declare at least one entry"
    # Expected shape per Claude Code's documented hook schema:
    # [{matcher, hooks: [{type: command, command: "..."}]}]
    first = stop_entries[0]
    assert first["matcher"] == "*"
    inner = first["hooks"]
    assert any(h.get("command", "").startswith("rationale capture") for h in inner)


def test_hooks_json_command_uses_quiet_flag() -> None:
    """The Stop hook should pass --quiet so Claude Code's session end
    isn't cluttered with capture chatter."""
    hooks = _load(REPO_ROOT / "hooks" / "hooks.json")
    cmd = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "--quiet" in cmd


def test_hooks_json_command_degrades_gracefully() -> None:
    """If the rationale CLI isn't on PATH yet (user installed the plugin
    before running `pip install rationale`), Claude Code's Stop hook
    must not surface a failure. We require the command to append
    ``|| true`` so the hook always exits 0."""
    hooks = _load(REPO_ROOT / "hooks" / "hooks.json")
    cmd = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd.rstrip().endswith("|| true"), (
        f"Stop hook command must end with '|| true' so a missing CLI "
        f"doesn't crash the session. Got: {cmd!r}"
    )


def test_hooks_json_entries_have_type_field() -> None:
    """Regression: every inner hook entry must declare type='command'.
    The ECC schema notes say a missing 'type' is silently accepted by
    some validator versions and rejected by others — pin it here."""
    hooks = _load(REPO_ROOT / "hooks" / "hooks.json")
    for stop_entry in hooks["hooks"]["Stop"]:
        for inner in stop_entry["hooks"]:
            assert inner.get("type") == "command", (
                f"inner hook entry missing type='command': {inner}"
            )


# --- commands/*.md ----------------------------------------------------------


_FRONTMATTER = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n",
    re.DOTALL,
)


def _command_files() -> list[Path]:
    return sorted((REPO_ROOT / "commands").glob("*.md"))


def test_at_least_one_command_ships() -> None:
    assert _command_files(), "expected at least one slash command"


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.name)
def test_command_file_has_frontmatter_with_description(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    assert match, f"{path.name}: missing YAML frontmatter block"
    fm = match.group("fm")
    assert re.search(r"^description:\s*.+$", fm, re.MULTILINE), (
        f"{path.name}: frontmatter must include a `description:` line"
    )


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.name)
def test_command_file_invokes_rationale_cli(path: Path) -> None:
    """Every slash command should shell out to the rationale CLI so the
    plugin contract stays thin: Python does the work, the command file
    is just a description + invocation."""
    assert "rationale" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.name)
def test_command_file_documents_pip_prerequisite(path: Path) -> None:
    """Every slash command must tell the user the prerequisite if the
    CLI isn't on PATH yet. The hook silently `|| true`s; the slash
    commands should explicitly say 'pip install rationale'."""
    text = path.read_text(encoding="utf-8").lower()
    assert "pip install rationale" in text, (
        f"{path.name}: missing `pip install rationale` prerequisite note"
    )

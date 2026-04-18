"""Tests for symbol extraction used by AST-style anchoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from rationale.symbols import content_hash, extract_symbols, find_symbol, symbol_at_line


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_python_extracts_functions_and_classes(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "mod.py",
        "def alpha():\n    return 1\n\n\nclass Beta:\n    def gamma(self):\n        return 2\n",
    )
    syms = extract_symbols(p)
    names = {s.name for s in syms}
    assert "alpha" in names
    assert "Beta" in names
    # Methods use dotted notation so they survive inside a class rename check
    assert "Beta.gamma" in names


def test_python_symbol_at_line_returns_enclosing(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "mod.py",
        "def alpha():\n    x = 1\n    return x\n\n\ndef beta():\n    return 2\n",
    )
    sym = symbol_at_line(p, 2)
    assert sym is not None
    assert sym.name == "alpha"
    sym = symbol_at_line(p, 6)
    assert sym is not None
    assert sym.name == "beta"


def test_python_symbol_at_line_outside_any_def(tmp_path: Path) -> None:
    p = _write(tmp_path, "mod.py", "X = 1\nY = 2\n")
    assert symbol_at_line(p, 1) is None


def test_javascript_like_extracts_functions(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "app.js",
        "function alpha(x) {\n  return x + 1;\n}\n\nconst beta = (y) => y * 2;\n",
    )
    syms = extract_symbols(p)
    names = {s.name for s in syms}
    assert "alpha" in names


def test_typescript_extracts_classes(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "svc.ts",
        "export class PaymentService {\n  retry() {\n    return 3;\n  }\n}\n",
    )
    syms = extract_symbols(p)
    names = {s.name for s in syms}
    assert "PaymentService" in names


def test_find_symbol_returns_same_object_after_relocation(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "mod.py",
        "\n\n\ndef target():\n    return 42\n",
    )
    before = find_symbol(p, "target")
    assert before is not None
    before_start = before.line_start

    # Pad the top of the file; symbol line range should shift
    p.write_text(
        "\n".join(["# pad"] * 30) + "\n\n" + p.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    after = find_symbol(p, "target")
    assert after is not None
    assert after.name == "target"
    assert after.line_start > before_start


def test_find_symbol_returns_none_when_removed(tmp_path: Path) -> None:
    p = _write(tmp_path, "mod.py", "def stays():\n    return 1\n")
    assert find_symbol(p, "missing") is None


def test_content_hash_stable_for_same_input() -> None:
    assert content_hash("hello\nworld") == content_hash("hello\nworld")


def test_content_hash_ignores_trailing_whitespace() -> None:
    # Line-normalized hashing: trailing whitespace on a line shouldn't
    # change the hash. Otherwise an autoformatter flips every anchor stale.
    assert content_hash("hello\n") == content_hash("hello   \n")


def test_content_hash_differs_for_different_content() -> None:
    assert content_hash("a") != content_hash("b")


def test_unknown_extension_returns_no_symbols(tmp_path: Path) -> None:
    p = _write(tmp_path, "data.unknown", "arbitrary contents")
    assert extract_symbols(p) == []


def test_missing_file_returns_no_symbols(tmp_path: Path) -> None:
    assert extract_symbols(tmp_path / "nope.py") == []


def test_python_syntax_error_degrades_gracefully(tmp_path: Path) -> None:
    p = _write(tmp_path, "broken.py", "def incomplete(:::\n")
    # Should not raise. Fallback may return [] or regex-best-effort — we only
    # assert no exception and a list result.
    result = extract_symbols(p)
    assert isinstance(result, list)


@pytest.mark.parametrize("ext", ["go", "rs"])
def test_other_language_funcs_are_captured(tmp_path: Path, ext: str) -> None:
    if ext == "go":
        body = "package x\n\nfunc Alpha() int {\n  return 1\n}\n"
    else:
        body = "fn alpha() -> i32 {\n    1\n}\n"
    p = _write(tmp_path, f"mod.{ext}", body)
    syms = extract_symbols(p)
    names = {s.name for s in syms}
    assert any(n.lower() == "alpha" for n in names)


def test_nested_function_inside_method_uses_method_prefix(tmp_path: Path) -> None:
    """Regression: a helper defined inside a method must include the method
    in its dotted name, not collapse to ClassName.helper."""
    p = _write(
        tmp_path,
        "mod.py",
        (
            "class Outer:\n"
            "    def method(self):\n"
            "        def helper():\n"
            "            return 1\n"
            "        return helper()\n"
        ),
    )
    names = {s.name for s in extract_symbols(p)}
    assert "Outer.method.helper" in names
    assert "Outer.helper" not in names  # Must not be misattributed


def test_javascript_arrow_function_is_captured(tmp_path: Path) -> None:
    """Regression: the JS regex lists arrow functions, but earlier tests
    only asserted `function alpha` — confirm the arrow form works too."""
    p = _write(
        tmp_path,
        "arrow.js",
        "const beta = (y) => y * 2;\nconst gamma = async (x) => x + 1;\n",
    )
    names = {s.name for s in extract_symbols(p)}
    assert "beta" in names
    assert "gamma" in names


def test_single_line_hash_range(tmp_path: Path) -> None:
    """hash_file_range must behave sanely when start == end."""
    from rationale.symbols import hash_file_range

    p = _write(tmp_path, "a.py", "one\ntwo\nthree\n")
    h = hash_file_range(p, 2, 2)
    assert h is not None
    # Hashing only the second line should equal hashing the string "two"
    assert h == content_hash("two")


def test_build_hook_config_has_docstring() -> None:
    """Regression: the docstring was mis-placed after `del root`."""
    from rationale.cli import build_hook_config

    assert build_hook_config.__doc__ is not None
    assert "Stop hook" in build_hook_config.__doc__

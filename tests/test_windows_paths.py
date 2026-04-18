"""Windows drive-letter paths must not silently downgrade `why` to text search."""

from __future__ import annotations

from rationale.query import LINE_REF


def test_line_ref_matches_drive_letter_path() -> None:
    m = LINE_REF.match("C:/src/payment.ts:42")
    assert m is not None
    assert m.group("file") == "C:/src/payment.ts"
    assert m.group("line") == "42"


def test_line_ref_matches_posix_path() -> None:
    m = LINE_REF.match("src/payment.ts:42")
    assert m is not None
    assert m.group("file") == "src/payment.ts"
    assert m.group("line") == "42"


def test_line_ref_rejects_non_line_ref() -> None:
    assert LINE_REF.match("just a term") is None
    # Pure text with no trailing number — stays in text search.
    assert LINE_REF.match("retry:backoff") is None

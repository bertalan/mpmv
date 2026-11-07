"""Generated docs end with exactly one newline (pre-commit end-of-file-fixer safe)."""

from pathlib import Path

import pytest

from tools import build_docs


def test_generated_text_ends_with_exactly_one_newline() -> None:
    tasks = build_docs.parse_tasks()
    for text in (build_docs.md_backlog(tasks), build_docs.csv_text(tasks)):
        assert text.endswith("\n")
        assert not text.endswith("\n\n")


def test_html_spec_ends_with_exactly_one_newline(tmp_path: Path) -> None:
    pytest.importorskip("markdown")
    out = tmp_path / "spec.html"
    build_docs.build_html([build_docs.SPEC / "README.md"], out)
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")

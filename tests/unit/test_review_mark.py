"""I18N.19 1/n: the per-language review mark (tool) and its system check."""

import importlib.util
import sys
from pathlib import Path

import pytest
from django.core.checks import Error, Warning
from django.test import override_settings

from apps.core.language_checks import REVIEW_HEADER, check_language_review

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_catalog", ROOT / "tools" / "i18n_catalog.py")
assert _spec is not None
assert _spec.loader is not None
catalog = importlib.util.module_from_spec(_spec)
sys.modules["i18n_catalog"] = catalog
_spec.loader.exec_module(catalog)

HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
SENSITIVE = '#: apps/orders/emails.py\nmsgid "Your order"\nmsgstr "Ihre Bestellung"\n'
PLAIN = '#. note\n#: apps/cms/blocks.py\nmsgid "Home"\nmsgstr "Start"\n'


def _write(root: Path, code: str, body: str) -> Path:
    path = root / code / "LC_MESSAGES" / "django.po"
    path.parent.mkdir(parents=True)
    path.write_text(f"#\n{HEADER}\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def de(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = _write(tmp_path, "de", SENSITIVE + "\n" + PLAIN)
    monkeypatch.setattr(catalog, "_po_path", lambda lang: path)
    return path


def test_mark_reviewed_marks_sensitive_entries_and_the_header(de: Path) -> None:
    assert catalog.mark_reviewed("de") == 1

    text = de.read_text(encoding="utf-8")
    assert REVIEW_HEADER in text.split("msgid", 1)[0]
    assert '# reviewed\n#: apps/orders/emails.py\nmsgid "Your order"' in text
    assert "# reviewed\n#: apps/cms" not in text


def test_mark_reviewed_changes_nothing_else_and_is_idempotent(de: Path) -> None:
    before = de.read_text(encoding="utf-8")
    catalog.mark_reviewed("de")
    once = de.read_text(encoding="utf-8")

    assert catalog.mark_reviewed("de") == 0
    assert de.read_text(encoding="utf-8") == once
    assert once.replace("# reviewed\n", "").replace(REVIEW_HEADER + "\n", "") == before


def test_marked_entries_count_as_reviewed(de: Path) -> None:
    catalog.mark_reviewed("de")

    rows = catalog.review_rows("de", pending=True)

    assert rows == []


def _messages(root: Path, codes: list[str], *, live: bool) -> list[object]:
    langs = [(c, c) for c in codes]
    with override_settings(
        LANGUAGE_CODE=codes[0], LANGUAGES=langs, LOCALE_PATHS=[root], LIVE_DATA=live
    ):
        return [m for m in check_language_review(None) if str(m.id).endswith("915")]


def test_an_unreviewed_listed_language_is_an_error_on_live(tmp_path: Path) -> None:
    _write(tmp_path, "de", SENSITIVE)

    messages = _messages(tmp_path, ["en", "de"], live=True)

    assert [type(m) for m in messages] == [Error]
    assert messages[0].id == "core.E915"  # type: ignore[attr-defined]


def test_an_unreviewed_listed_language_is_a_warning_elsewhere(tmp_path: Path) -> None:
    _write(tmp_path, "de", SENSITIVE)

    messages = _messages(tmp_path, ["en", "de"], live=False)

    assert [type(m) for m in messages] == [Warning]


def test_a_marked_catalog_passes_on_live(tmp_path: Path) -> None:
    path = _write(tmp_path, "de", SENSITIVE)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("\nmsgid", f"\n{REVIEW_HEADER}\nmsgid", 1), encoding="utf-8")

    assert _messages(tmp_path, ["en", "de"], live=True) == []


def test_english_and_italian_need_no_mark_and_the_default_stays_clean(tmp_path: Path) -> None:
    _write(tmp_path, "it", SENSITIVE)

    assert _messages(tmp_path, ["en", "it"], live=True) == []

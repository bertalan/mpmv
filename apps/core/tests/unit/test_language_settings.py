"""I18N.15: the site languages come from MPMV_LANGUAGES and are checked at startup.

`mpmv/settings/languages.py` turns the ordered list into LANGUAGE_CODE and LANGUAGES (the first
code is the primary language); `core.E911` to `core.E913` and `core.W911` refuse or flag a bad
list. Nothing here assumes English is among the languages.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from django.core.checks import CheckMessage, Error, Info, Warning
from django.test import override_settings

from apps.core.language_checks import check_language_settings
from mpmv.settings.languages import DEFAULT_LANGUAGE_CODES, language_settings

PO_HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'


def _catalog(root: Path, code: str, entries: str) -> None:
    folder = root / code / "LC_MESSAGES"
    folder.mkdir(parents=True)
    (folder / "django.po").write_text(PO_HEADER + entries, encoding="utf-8")


def _ids(messages: Sequence[CheckMessage]) -> list[str]:
    return [str(message.id) for message in messages]


def test_an_empty_list_means_the_default_languages() -> None:
    code, languages = language_settings([])

    assert code == "en"
    assert [c for c, _name in languages] == list(DEFAULT_LANGUAGE_CODES)


def test_the_first_code_is_the_primary_language_and_names_are_in_their_own_language() -> None:
    code, languages = language_settings(["it", "de"])

    assert code == "it"
    assert languages == [("it", "Italiano"), ("de", "Deutsch")]


def test_codes_are_trimmed_and_lowercased() -> None:
    assert language_settings([" DE ", "It"])[1] == [("de", "Deutsch"), ("it", "Italiano")]


def test_an_unknown_code_is_kept_for_the_check_to_refuse() -> None:
    assert language_settings(["it", "xx"])[1][1] == ("xx", "xx")


@override_settings(LANGUAGE_CODE="it", LANGUAGES=[("it", "Italiano"), ("xx", "xx")])
def test_an_unknown_language_code_is_refused() -> None:
    errors = [m for m in check_language_settings(None) if isinstance(m, Error)]

    assert _ids(errors) == ["core.E911"]
    assert "xx" in errors[0].msg


@override_settings(LANGUAGE_CODE="it", LANGUAGES=[("it", "Italiano"), ("it", "Italiano")])
def test_a_repeated_language_code_is_refused() -> None:
    assert "core.E912" in _ids(check_language_settings(None))


@override_settings(LANGUAGE_CODE="de", LANGUAGES=[("it", "Italiano"), ("de", "Deutsch")])
def test_the_primary_language_is_the_first_one() -> None:
    assert "core.E913" in _ids(check_language_settings(None))


def test_a_missing_catalog_warns_for_a_language_outside_the_default_set(tmp_path: Path) -> None:
    with override_settings(
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("pt", "Português")],
        LOCALE_PATHS=[tmp_path],
    ):
        messages = check_language_settings(None)

    assert [type(m) for m in messages] == [Warning]
    assert messages[0].id == "core.W911"
    assert "pt" in messages[0].msg


def test_an_incomplete_catalog_warns_with_the_gap(tmp_path: Path) -> None:
    _catalog(tmp_path, "pt", 'msgid "a"\nmsgstr "x"\n\nmsgid "b"\nmsgstr ""\n\n')
    _catalog(tmp_path, "nl", '#, fuzzy\nmsgid "a"\nmsgstr "x"\n\n')
    with override_settings(
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("pt", "Português"), ("nl", "Nederlands")],
        LOCALE_PATHS=[tmp_path],
    ):
        messages = check_language_settings(None)

    assert _ids(messages) == ["core.W911", "core.W911"]
    assert "1 of 2" in messages[0].msg
    assert "1 of 1" in messages[1].msg


def test_a_complete_catalog_passes(tmp_path: Path) -> None:
    _catalog(tmp_path, "pt", 'msgid "a"\nmsgstr "x"\n\n')
    with override_settings(
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("pt", "Português")],
        LOCALE_PATHS=[tmp_path],
    ):
        assert check_language_settings(None) == []


def test_english_needs_no_catalog_because_the_msgids_are_english(tmp_path: Path) -> None:
    with override_settings(
        LANGUAGE_CODE="en", LANGUAGES=[("en", "English")], LOCALE_PATHS=[tmp_path]
    ):
        assert check_language_settings(None) == []


def test_languages_still_waiting_for_i18n7_are_an_info_not_a_warning(tmp_path: Path) -> None:
    with override_settings(
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("de", "Deutsch")],
        LOCALE_PATHS=[tmp_path],
    ):
        messages = check_language_settings(None)

    assert _ids(messages) == ["core.I911"]
    assert [type(m) for m in messages] == [Info]


def test_the_shipped_configuration_passes_without_errors_or_warnings() -> None:
    from django.conf import settings

    messages = check_language_settings(None)

    assert not [m for m in messages if isinstance(m, Error | Warning)], settings.LANGUAGES


@pytest.mark.parametrize("codes", [["it", "de"], ["de", "it"], ["fr"]])
def test_every_non_english_configuration_is_well_formed(codes: list[str]) -> None:
    code, languages = language_settings(codes)

    assert code == codes[0]
    assert [c for c, _name in languages] == codes

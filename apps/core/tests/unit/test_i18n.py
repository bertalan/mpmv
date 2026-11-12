"""I18N.1: translating a path into another configured language keeps the page (spec 09 §9.2).

`/` is EN (no prefix), `/it/`, `/de/`, `/fr/` and `/es/` follow; paths outside the
language-prefixed routes (health check, staff admin) are returned unchanged. Any other path is a
CMS page path (CMS.1): it moves under the other prefix, slug unchanged. No database involved.
"""

from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings

from apps.core.i18n import (
    configured_languages,
    language_choices,
    primary_language,
    translate_path,
)


def _primary() -> str:
    return str(settings.LANGUAGE_CODE)


def _other() -> str:
    """A configured language that is not the primary one."""
    return configured_languages()[1]


def test_translate_path_swaps_the_language_prefix() -> None:
    primary, other = _primary(), _other()

    assert translate_path("/", other) == f"/{other}/"
    assert translate_path("/", primary) == "/"
    assert translate_path(f"/{other}/", primary) == "/"
    assert translate_path(f"/{other}/", other) == f"/{other}/"
    for code in configured_languages()[1:]:
        assert translate_path(f"/{other}/", code) == f"/{code}/"
        assert translate_path(f"/{code}/", primary) == "/"


def test_translate_path_keeps_the_query_string() -> None:
    other = _other()

    assert translate_path(f"/{other}/?page=2", _primary()) == "/?page=2"
    assert translate_path("/?page=2", other) == f"/{other}/?page=2"


@pytest.mark.parametrize("path", ["/healthz", "/django-admin/"])
def test_translate_path_leaves_unprefixed_routes_alone(path: str) -> None:
    for language in configured_languages():
        assert translate_path(path, language) == path


def test_translate_path_moves_page_paths_under_the_other_prefix() -> None:
    # The CMS language switcher sends the translated page's own URL (slugs differ by language).
    primary, other = _primary(), _other()

    assert translate_path("/about/", other) == f"/{other}/about/"
    assert translate_path(f"/{other}/chi-siamo/", primary) == "/chi-siamo/"


def test_translate_path_ignores_unknown_languages() -> None:
    assert translate_path(f"/{_other()}/", "xx") == f"/{_other()}/"


def test_a_language_added_in_settings_is_routed_without_code_changes(settings: Any) -> None:
    settings.LANGUAGES = [*settings.LANGUAGES, ("pt", "Português")]
    primary, other = _primary(), _other()

    assert translate_path("/", "pt") == "/pt/"
    assert translate_path("/pt/", other) == f"/{other}/"
    assert translate_path("/pt/", primary) == "/"


def test_language_choices_are_read_from_settings_in_order(settings: Any) -> None:
    settings.LANGUAGES = [("de", "Deutsch"), ("fr", "Français"), ("es", "Español")]

    assert language_choices() == [("de", "Deutsch"), ("fr", "Français"), ("es", "Español")]


def test_language_choices_follow_settings_at_runtime(settings: Any) -> None:
    settings.LANGUAGES = [*settings.LANGUAGES, ("pt", "Português")]

    assert [code for code, _name in language_choices()] == [*configured_languages()]
    assert configured_languages()[-1] == "pt"


def test_the_primary_language_default_follows_the_setting_at_call_time() -> None:
    with override_settings(LANGUAGE_CODE="it"):
        assert primary_language() == "it"
    with override_settings(LANGUAGE_CODE="de"):
        assert primary_language() == "de"

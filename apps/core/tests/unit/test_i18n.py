"""I18N.1: translating a path into another configured language keeps the page (spec 09 §9.2).

`/` is EN (no prefix), `/it/`, `/de/`, `/fr/` and `/es/` follow; paths outside the
language-prefixed routes (health check, staff admin) are returned unchanged. Any other path is a
CMS page path (CMS.1): it moves under the other prefix, slug unchanged. No database involved.
"""

import pytest
from django.test import override_settings

from apps.core.i18n import language_choices, translate_path

LANGUAGES_PLUS_PORTUGUESE = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
]


@pytest.mark.parametrize(
    ("path", "language", "expected"),
    [
        ("/", "it", "/it/"),
        ("/", "de", "/de/"),
        ("/", "fr", "/fr/"),
        ("/", "es", "/es/"),
        ("/", "en", "/"),
        ("/it/", "de", "/de/"),
        ("/it/", "en", "/"),
        ("/de/", "it", "/it/"),
        ("/de/", "en", "/"),
        ("/it/", "it", "/it/"),
        ("/fr/", "es", "/es/"),
        ("/es/", "en", "/"),
    ],
)
def test_translate_path_swaps_the_language_prefix(path: str, language: str, expected: str) -> None:
    assert translate_path(path, language) == expected


def test_translate_path_keeps_the_query_string() -> None:
    assert translate_path("/it/?page=2", "de") == "/de/?page=2"
    assert translate_path("/?page=2", "it") == "/it/?page=2"


@pytest.mark.parametrize("path", ["/healthz", "/django-admin/"])
@pytest.mark.parametrize("language", ["en", "it", "de", "fr", "es"])
def test_translate_path_leaves_unprefixed_routes_alone(path: str, language: str) -> None:
    assert translate_path(path, language) == path


@pytest.mark.parametrize(
    ("path", "language", "expected"),
    [("/about/", "it", "/it/about/"), ("/it/chi-siamo/", "en", "/chi-siamo/")],
)
def test_translate_path_moves_page_paths_under_the_other_prefix(
    path: str, language: str, expected: str
) -> None:
    # The CMS language switcher sends the translated page's own URL (slugs differ by language).
    assert translate_path(path, language) == expected


def test_translate_path_ignores_unknown_languages() -> None:
    assert translate_path("/it/", "xx") == "/it/"


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_a_language_added_in_settings_is_routed_without_code_changes() -> None:
    assert translate_path("/", "pt") == "/pt/"
    assert translate_path("/pt/", "it") == "/it/"
    assert translate_path("/pt/", "en") == "/"


def test_language_choices_are_read_from_settings_in_order() -> None:
    assert language_choices() == [
        ("en", "English"),
        ("it", "Italiano"),
        ("de", "Deutsch"),
        ("fr", "Français"),
        ("es", "Español"),
    ]


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_language_choices_follow_settings_at_runtime() -> None:
    assert [code for code, _name in language_choices()] == ["en", "it", "de", "fr", "es", "pt"]

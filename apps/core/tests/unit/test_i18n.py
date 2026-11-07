"""I18N.1: translating a path into another configured language keeps the page (spec 09 §9.2).

`/` is EN (no prefix), `/it/` and `/de/` follow; paths outside the language-prefixed routes
(health check, staff admin, unknown pages) are returned unchanged. No database involved.
"""

import pytest
from django.test import override_settings

from apps.core.i18n import translate_path

LANGUAGES_PLUS_FRENCH = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
]


@pytest.mark.parametrize(
    ("path", "language", "expected"),
    [
        ("/", "it", "/it/"),
        ("/", "de", "/de/"),
        ("/", "en", "/"),
        ("/it/", "de", "/de/"),
        ("/it/", "en", "/"),
        ("/de/", "it", "/it/"),
        ("/de/", "en", "/"),
        ("/it/", "it", "/it/"),
    ],
)
def test_translate_path_swaps_the_language_prefix(path: str, language: str, expected: str) -> None:
    assert translate_path(path, language) == expected


def test_translate_path_keeps_the_query_string() -> None:
    assert translate_path("/it/?page=2", "de") == "/de/?page=2"
    assert translate_path("/?page=2", "it") == "/it/?page=2"


@pytest.mark.parametrize("path", ["/healthz", "/django-admin/", "/no-such-page/"])
@pytest.mark.parametrize("language", ["en", "it", "de"])
def test_translate_path_leaves_unprefixed_routes_alone(path: str, language: str) -> None:
    assert translate_path(path, language) == path


def test_translate_path_ignores_unknown_languages() -> None:
    assert translate_path("/it/", "xx") == "/it/"


@override_settings(LANGUAGES=LANGUAGES_PLUS_FRENCH)
def test_a_language_added_in_settings_is_routed_without_code_changes() -> None:
    assert translate_path("/", "fr") == "/fr/"
    assert translate_path("/fr/", "it") == "/it/"
    assert translate_path("/fr/", "en") == "/"

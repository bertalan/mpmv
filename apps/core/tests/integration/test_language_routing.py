"""I18N.1: language-prefix routing (spec 09 §9.2). Done when: every configured prefix is active.

`/` = EN (primary, no prefix), then `/it/`, `/de/`, `/fr/`, `/es/`; the languages come only from
settings.LANGUAGES.
"""

import pytest
from django.test import Client, override_settings
from django.urls import resolve, reverse
from django.utils import translation

from apps.core.i18n import configured_languages, primary_language
from tests.i18n import language_prefix, other_language, third_language

pytestmark = pytest.mark.django_db

if len(configured_languages()) < 3:  # I18N.15 part 3: these cases name three languages
    pytest.skip("needs three configured languages", allow_module_level=True)

LANGUAGES_PLUS_PORTUGUESE = [
    (primary_language(), "English"),
    (other_language(), "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
]

LANGUAGE_PATHS = [
    (primary_language(), "/"),
    (other_language(), f"{language_prefix(other_language())}/"),
    (third_language(), f"{language_prefix(third_language())}/"),
    ("fr", "/fr/"),
    ("es", "/es/"),
]
PATH_LANGUAGES = [(path, language) for language, path in LANGUAGE_PATHS]


@pytest.mark.parametrize(("language", "path"), LANGUAGE_PATHS)
def test_home_reverses_with_the_language_prefix(language: str, path: str) -> None:
    with translation.override(language):
        assert reverse("home") == path


@pytest.mark.parametrize(("language", "path"), LANGUAGE_PATHS)
def test_home_resolves_in_each_language(language: str, path: str) -> None:
    with translation.override(language):
        assert resolve(path).url_name == "home"


@pytest.mark.parametrize(("path", "language"), PATH_LANGUAGES)
def test_each_language_prefix_serves_the_page_in_that_language(
    client: Client, path: str, language: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response["Content-Language"] == language
    assert f'<html lang="{language}"' in response.content.decode()


def test_the_primary_language_has_no_prefix(client: Client) -> None:
    assert client.get(f"/{primary_language()}/").status_code == 404


def test_unconfigured_language_prefixes_are_not_routed(client: Client) -> None:
    assert client.get("/pt/").status_code == 404


def test_browser_language_does_not_redirect_the_unprefixed_home(client: Client) -> None:
    response = client.get("/", headers={"accept-language": "it,de;q=0.8"})
    assert response.status_code == 200
    assert response["Content-Language"] == primary_language()


@pytest.mark.parametrize(
    "path",
    [
        f"{language_prefix(other_language())}/healthz",
        f"{language_prefix(third_language())}/healthz",
        "/fr/healthz",
        "/es/healthz",
        f"{language_prefix(other_language())}/django-admin/",
    ],
)
def test_operational_routes_stay_outside_the_language_prefixes(client: Client, path: str) -> None:
    # Under a prefix the path is a CMS page lookup (CMS.1), after the trailing-slash redirect.
    assert client.get(path, follow=True).status_code == 404


def test_healthz_and_staff_admin_answer_without_prefix(client: Client) -> None:
    assert client.get("/healthz", headers={"accept-language": "de"}).status_code == 200
    assert client.get("/django-admin/login/").status_code == 200


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_a_language_added_in_settings_is_served_without_code_changes(client: Client) -> None:
    response = client.get("/pt/")
    assert response.status_code == 200
    assert response["Content-Language"] == "pt"
    assert 'value="pt"' in response.content.decode()  # offered by the switcher too


@pytest.mark.parametrize(("path", "current"), PATH_LANGUAGES)
def test_home_shows_a_switcher_for_every_configured_language(
    client: Client, path: str, current: str
) -> None:
    html = client.get(path + "?page=2").content.decode()
    assert f'action="{reverse("switch_language")}"' in html
    assert f'name="next" value="{path}?page=2"' in html
    for code in (primary_language(), other_language(), "de", "fr", "es"):
        assert f'name="language" value="{code}"' in html
    assert f'value="{current}" lang="{current}" aria-current="true"' in html

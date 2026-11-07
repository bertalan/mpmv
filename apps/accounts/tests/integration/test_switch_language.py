"""I18N.1: the language switcher keeps the current page and remembers a signed-in user's choice."""

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import set_preferred_language
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LANGUAGES_PLUS_PORTUGUESE = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("pt", "Português"),
]


def _switch(client: Client, language: str, next_url: str) -> str:
    response = client.post(reverse("switch_language"), {"language": language, "next": next_url})
    assert response.status_code == 302
    return str(response["Location"])


@pytest.mark.parametrize(
    ("next_url", "language", "expected"),
    [
        ("/", "it", "/it/"),
        ("/", "de", "/de/"),
        ("/it/", "de", "/de/"),
        ("/it/", "en", "/"),
        ("/de/", "it", "/it/"),
        ("/", "fr", "/fr/"),
        ("/fr/", "es", "/es/"),
        ("/es/?page=2", "en", "/?page=2"),
        ("/de/?page=2", "en", "/?page=2"),
    ],
)
def test_switcher_keeps_the_current_page(
    client: Client, next_url: str, language: str, expected: str
) -> None:
    assert _switch(client, language, next_url) == expected


def test_switcher_follows_to_a_page_in_the_new_language(client: Client) -> None:
    response = client.post(
        reverse("switch_language"), {"language": "de", "next": "/it/"}, follow=True
    )
    assert response.status_code == 200
    assert response["Content-Language"] == "de"


def test_switcher_never_redirects_off_site(client: Client) -> None:
    assert _switch(client, "it", "https://attacker.example/") == "/it/"


def test_switcher_ignores_unconfigured_languages(client: Client) -> None:
    assert _switch(client, "xx", "/it/") == "/it/"


@override_settings(LANGUAGES=LANGUAGES_PLUS_PORTUGUESE)
def test_switcher_offers_a_language_added_in_settings(client: Client) -> None:
    assert _switch(client, "pt", "/de/") == "/pt/"


def test_switcher_only_accepts_post(client: Client) -> None:
    assert client.get(reverse("switch_language"), {"language": "it"}).status_code == 405


def test_switcher_remembers_the_choice_of_a_signed_in_user(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)
    assert _switch(client, "de", "/it/") == "/de/"
    user.refresh_from_db()
    assert user.preferred_language == "de"


def test_switcher_works_for_anonymous_visitors_without_storing_anything(client: Client) -> None:
    assert _switch(client, "de", "/") == "/de/"
    assert not User.objects.exists()


def test_an_explicit_prefix_wins_over_the_preferred_language(client: Client) -> None:
    user = UserFactory.create(preferred_language="de")
    client.force_login(user)
    response = client.get("/it/")
    assert response.status_code == 200
    assert response["Content-Language"] == "it"


def test_set_preferred_language_dry_run_returns_the_change_without_saving() -> None:
    user = UserFactory.create()
    changeset = set_preferred_language(user, "it", dry_run=True)
    assert changeset.summary() == {"create": 0, "update": 1, "delete": 0}
    assert changeset.changes[0].fields == {"preferred_language": ("en", "it")}
    user.refresh_from_db()
    assert user.preferred_language == "en"


def test_set_preferred_language_saves_and_is_a_no_op_when_unchanged() -> None:
    user = UserFactory.create()
    assert not set_preferred_language(user, "de").is_empty
    user.refresh_from_db()
    assert user.preferred_language == "de"
    assert set_preferred_language(user, "de").is_empty


def test_set_preferred_language_rejects_unconfigured_languages() -> None:
    user = UserFactory.create()
    with pytest.raises(ValueError, match="xx"):
        set_preferred_language(user, "xx")

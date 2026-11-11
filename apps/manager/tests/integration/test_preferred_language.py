"""I18N.12: a signed-in user's `preferred_language` decides the language of unprefixed areas.

The language switcher sets no cookie, so on the Manager, the account area and the admins (no
language prefix) the stored preference is read back. Prefixed URLs and anonymous visitors keep
their usual sources (prefix, browser language).
"""

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

MANAGER = "/manager/shop/vendor/"


def _html_lang(html: str) -> str:
    found = re.search(r'<html lang="([^"]+)"', html)
    assert found, "no <html lang>"
    return found.group(1)


@pytest.fixture
def member(db: None) -> User:
    return VendorMemberFactory.create(role="owner").user


def _switch(client: Client, language: str, next_url: str) -> None:
    response = client.post(
        reverse("switch_language"),
        {"language": language, "next": next_url},
        headers={"accept-language": "en"},
    )
    assert response.status_code == 302
    assert response["Location"] == next_url


def test_switching_language_in_the_manager_renders_it_in_that_language(
    client: Client, member: User
) -> None:
    verified_login(client, member)
    en = client.get(MANAGER, headers={"accept-language": "en"})
    assert _html_lang(en.text) == "en"

    _switch(client, "it", MANAGER)
    it = client.get(MANAGER, headers={"accept-language": "en"})
    assert it.status_code == 200
    assert _html_lang(it.text) == "it"
    assert it["Content-Language"] == "it"

    _switch(client, "en", MANAGER)
    back = client.get(MANAGER, headers={"accept-language": "en"})
    assert _html_lang(back.text) == "en"
    assert back["Content-Language"] == "en"


def test_the_stored_language_applies_to_the_account_area_too(client: Client) -> None:
    user = UserFactory.create(preferred_language="de")
    client.force_login(user)
    response = client.get("/account/login/", headers={"accept-language": "en"}, follow=True)
    assert _html_lang(response.text) == "de"


def test_a_prefixed_public_url_ignores_the_stored_language(client: Client) -> None:
    client.force_login(UserFactory.create(preferred_language="it"))
    english = client.get("/", headers={"accept-language": "en"})
    assert _html_lang(english.text) == "en"
    german = client.get("/de/", headers={"accept-language": "en"})
    assert _html_lang(german.text) == "de"
    italian_stays = client.get("/it/", headers={"accept-language": "de"})
    assert _html_lang(italian_stays.text) == "it"


def test_anonymous_on_an_unprefixed_page_is_unchanged(client: Client) -> None:
    """Unprefixed routes are the primary language (LocaleMiddleware); nothing changes here."""
    response = client.get("/account/login/", headers={"accept-language": "fr"})
    assert _html_lang(response.text) == "en"


def test_an_unknown_stored_language_falls_back_silently(client: Client, member: User) -> None:
    User.objects.filter(pk=member.pk).update(preferred_language="xx")
    verified_login(client, member)
    response = client.get(MANAGER, headers={"accept-language": "fr"})
    assert response.status_code == 200
    assert _html_lang(response.text) == "en"


def test_a_language_removed_from_settings_falls_back(
    client: Client, member: User, settings: SettingsWrapper
) -> None:
    User.objects.filter(pk=member.pk).update(preferred_language="es")
    settings.LANGUAGES = [(c, n) for c, n in settings.LANGUAGES if c != "es"]
    verified_login(client, member)
    response = client.get(MANAGER, headers={"accept-language": "en"})
    assert _html_lang(response.text) == "en"
    assert translation.get_language() is not None

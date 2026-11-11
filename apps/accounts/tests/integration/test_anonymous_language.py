"""I18N.11 (A): an anonymous visitor's language choice on the unprefixed pages (sign-in, MFA
steps) lives in the session, never in a language cookie. A signed-in user's stored preference
wins over it, and a login seeds the preference only when the user never chose one."""

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

KEY = "mpmv_language"


def _switch(client: Client, language: str, nxt: str = "/account/login/") -> object:
    return client.post(reverse("switch_language"), {"language": language, "next": nxt})


def _lang(response: object) -> str:
    html = response.content.decode()  # type: ignore[attr-defined]
    return str(html.split('<html lang="', 1)[1].split('"', 1)[0])


def test_an_anonymous_choice_is_kept_in_the_session_and_no_cookie(client: Client) -> None:
    response = _switch(client, "it")
    assert client.session[KEY] == "it"
    assert settings.LANGUAGE_COOKIE_NAME not in response.cookies  # type: ignore[attr-defined]
    assert _lang(client.get("/account/login/")) == "it"


def test_an_unknown_language_is_ignored(client: Client) -> None:
    _switch(client, "xx")
    assert KEY not in client.session


def test_the_choice_does_not_touch_the_prefixed_pages(client: Client) -> None:
    _switch(client, "it")
    assert _lang(client.get("/")) == "en"  # the prefix is the only source there


def test_the_stored_preference_of_a_signed_in_user_wins_over_the_session(client: Client) -> None:
    user = UserFactory.create(preferred_language="de")
    client.force_login(user)
    session = client.session
    session[KEY] = "it"
    session.save()
    assert _lang(client.get(reverse("mfa:overview"))) == "de"


def test_a_login_seeds_a_preference_that_was_never_chosen() -> None:
    client = Client()
    _switch(client, "it")
    user = UserFactory.create()  # the default preference: never chosen
    client.force_login(user)
    user.refresh_from_db()
    assert user.preferred_language == "it"


def test_a_login_keeps_a_preference_the_user_chose() -> None:
    client = Client()
    _switch(client, "it")
    user = UserFactory.create(preferred_language="fr")
    client.force_login(user)
    user.refresh_from_db()
    assert user.preferred_language == "fr"


def test_a_switch_by_a_signed_in_user_stores_the_preference_not_the_session(client: Client) -> None:
    user: User = UserFactory.create()
    client.force_login(user)
    _switch(client, "es")
    user.refresh_from_db()
    assert user.preferred_language == "es"
    assert KEY not in client.session


def test_the_switcher_is_rate_limited_per_address(client: Client) -> None:
    from apps.accounts.views import SWITCH_LIMIT

    for _ in range(SWITCH_LIMIT):
        assert _switch(client, "it").status_code == 302  # type: ignore[attr-defined]
    blocked = _switch(client, "en")
    assert blocked.status_code == 429  # type: ignore[attr-defined]
    assert int(blocked["Retry-After"]) >= 1  # type: ignore[index]
    assert client.session[KEY] == "it"  # the refused request changed nothing
    assert _switch(Client(REMOTE_ADDR="203.0.113.9"), "it").status_code == 302  # type: ignore[attr-defined]


def test_a_repeated_choice_does_not_rewrite_the_session(client: Client) -> None:
    _switch(client, "it")
    session = client.session
    session.modified = False
    _switch(client, "it")
    assert client.session[KEY] == "it"

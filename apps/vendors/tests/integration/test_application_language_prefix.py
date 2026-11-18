"""I18N.11 (C): the "Sell with us" pages live in `i18n_patterns`, like every public page.

The primary language keeps the old URLs (`/vendor/apply/...` is the English URL, so a link or a
bookmark made before the move still works with no redirect: a redirect by the browser language
would make the English page unreachable and loop with the language switcher). The other languages
get `/it/vendor/apply/...`. A link already sent by email goes through the action link, which
resolves the page URL when it is opened, so it leads to the prefixed page of the visitor's
language.
"""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.core.i18n import primary_language
from apps.vendors.tests.integration.test_application import (
    IP,
    applied,
    confirm_url,
    new_password,
)
from apps.vendors.tests.integration.test_claim import open_link
from tests.i18n import language_prefix, other_language, third_language

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("code", [other_language(), "de", "fr", "es"])
def test_the_pages_exist_in_every_language(client: Client, code: str) -> None:
    for name in ("vendors:apply", "vendors:apply_sent"):
        with translation.override(code):
            url = reverse(name)
        assert url.startswith(f"/{code}/vendor/apply/")
        response = client.get(url)
        assert response.status_code == 200
        assert f'<html lang="{code}"' in response.content.decode()


def test_the_english_urls_are_the_old_unprefixed_ones(client: Client) -> None:
    with translation.override(primary_language()):
        assert reverse("vendors:apply") == "/vendor/apply/"
        assert reverse("vendors:apply_sent") == "/vendor/apply/sent/"
    response = client.get("/vendor/apply/", HTTP_ACCEPT_LANGUAGE=other_language())
    assert response.status_code == 200  # no redirect to /it/: this is the English page


def test_an_emailed_link_opens_the_page_in_the_language_chosen_in_the_session(
    client: Client,
) -> None:
    """The link itself (`/account/link/...`) has no prefix, so the language comes from the
    language chosen in the session (else English); the page it leads to is in that language."""
    _, user, path = applied(client)
    english = Client()
    assert open_link(english, path)["Location"] == confirm_url(user)
    italian = Client()
    italian.post(reverse("switch_language"), {"language": other_language(), "next": "/"})
    response = open_link(italian, path)
    assert (
        response["Location"]
        == f"{language_prefix(other_language())}/vendor/apply/confirm/{user.public_id}/"
    )
    page = italian.get(response["Location"])
    assert page.status_code == 200
    assert f'lang="{other_language()}"' in page.content.decode()


def test_a_link_opened_before_the_move_still_confirms_through_the_old_url(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    old_url = f"/vendor/apply/confirm/{user.public_id}/"
    assert client.get(old_url).status_code == 200
    response = client.post(old_url, {"password": new_password()}, REMOTE_ADDR=IP)
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active is True


def test_the_prefixed_confirm_page_works_in_the_same_session(client: Client) -> None:
    _, user, path = applied(client)
    open_link(client, path)
    assert (
        client.get(
            f"{language_prefix(third_language())}/vendor/apply/confirm/{user.public_id}/"
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    ("code", "button"),
    [
        ("it", "Invia la mia candidatura"),
        ("de", "Meine Bewerbung senden"),
        ("fr", "Envoyer ma candidature"),
        ("es", "Enviar mi solicitud"),
    ],
)
def test_the_apply_form_button_is_translated(client: Client, code: str, button: str) -> None:
    with translation.override(code):
        url = reverse("vendors:apply")
    html = client.get(url).content.decode()
    assert button in html
    assert "Send my application" not in html

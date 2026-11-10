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

from apps.vendors.tests.integration.test_application import (
    IP,
    applied,
    confirm_url,
    new_password,
)
from apps.vendors.tests.integration.test_claim import open_link

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("code", ["it", "de", "fr", "es"])
def test_the_pages_exist_in_every_language(client: Client, code: str) -> None:
    for name in ("vendors:apply", "vendors:apply_sent"):
        with translation.override(code):
            url = reverse(name)
        assert url.startswith(f"/{code}/vendor/apply/")
        response = client.get(url)
        assert response.status_code == 200
        assert f'<html lang="{code}"' in response.content.decode()


def test_the_english_urls_are_the_old_unprefixed_ones(client: Client) -> None:
    assert reverse("vendors:apply") == "/vendor/apply/"
    assert reverse("vendors:apply_sent") == "/vendor/apply/sent/"
    response = client.get("/vendor/apply/", HTTP_ACCEPT_LANGUAGE="it")
    assert response.status_code == 200  # no redirect to /it/: this is the English page


def test_an_emailed_link_opens_the_prefixed_page_of_the_visitors_language(client: Client) -> None:
    _, user, path = applied(client)
    opened = Client()
    response = open_link(opened, path)
    assert response["Location"] == confirm_url(user)  # English visitor: the old URL
    other = Client()
    response = other.get(path, HTTP_ACCEPT_LANGUAGE="it")
    response = other.post(
        reverse("action_link:continue"),
        {"token": path.rstrip("/").rsplit("/", 1)[1]},
        HTTP_ACCEPT_LANGUAGE="it",
    )
    assert response["Location"] == f"/it/vendor/apply/confirm/{user.public_id}/"
    page = other.get(response["Location"])
    assert page.status_code == 200
    assert 'lang="it"' in page.content.decode()


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
    assert client.get(f"/de/vendor/apply/confirm/{user.public_id}/").status_code == 200

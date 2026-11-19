"""I18N.20 part 3b, slice 1: the withdrawal and consumer pages show the visitor's language.

The expected text comes from `gettext` under `translation.override`, so the tests hold for any
`MPMV_LANGUAGES` (CI also runs `it,de`) and name no language.
"""

import html

import pytest
from django.test import Client
from django.utils import translation
from django.utils.translation import gettext

from apps.cms.services import seed_site
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()


def _say(lang: str, text: str) -> str:
    with translation.override(lang):
        return gettext(text)


def _page(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return html.unescape(response.content.decode())


def test_the_withdrawal_page_speaks_the_page_language(client: Client) -> None:
    lang = other_language()
    body = _page(client, f"{language_prefix(lang)}/withdrawal/")
    text = (
        "Enter the number of your order and the email address you used. "
        "Next you choose what to withdraw from and confirm."
    )
    assert _say(lang, text) != text
    assert _say(lang, text) in body


def test_the_consumer_information_page_speaks_the_page_language(client: Client) -> None:
    lang = other_language()
    body = _page(client, f"{language_prefix(lang)}/consumer-information/")
    assert _say(lang, "Legal guarantee of conformity") in body

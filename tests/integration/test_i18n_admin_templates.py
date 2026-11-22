"""I18N.20 part 3b, slice 4: admin, editor and ride error templates speak the active language.

The expected text comes from `gettext` under `translation.override`, so the tests hold for any
`MPMV_LANGUAGES` and name no language. They fail while a template string is missing from the
catalog.
"""

import html

import pytest
from django.test import Client
from django.utils import translation
from django.utils.translation import gettext

from apps.cms.services import seed_site
from apps.themes.services import use_theme
from tests.i18n import other_language

pytestmark = pytest.mark.django_db


def _say(lang: str, text: str) -> str:
    with translation.override(lang):
        return gettext(text)


def test_the_ride_error_page_button_speaks_the_page_language(client: Client) -> None:
    seed_site()
    lang = other_language()
    label = _say(lang, "Go to the home page")
    assert label != "Go to the home page"
    with use_theme("ride"):
        response = client.get(f"/{lang}/no-such-page-i18n-20/")
    assert response.status_code == 404
    assert label in html.unescape(response.content.decode())


def test_the_admin_template_labels_differ_from_the_english_source() -> None:
    lang = other_language()
    for text in ("Failed render jobs", "Events awaiting review", "Hide event", "Merge"):
        assert _say(lang, text) != text, text

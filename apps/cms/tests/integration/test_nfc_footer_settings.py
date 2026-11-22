"""I18N.10: the footer settings admin form stores its text in Unicode NFC."""

import unicodedata
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.admin.panels import get_edit_handler
from wagtail.models import Site
from wagtail.test.utils.form_data import inline_formset, nested_form_data, streamfield

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import FooterColumn, FooterSettings, SocialProfile
from apps.core.i18n import primary_language
from apps.core.nfc_forms import NFCFormMixin

pytestmark = pytest.mark.django_db

COMPOSED = "Café à la carte"
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)
URL = "https://example.org/café?q=é"


@pytest.fixture
def admin(client: Client) -> Client:
    verified_login(client, UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _edit_url() -> str:
    site = Site.objects.get(is_default_site=True)
    return reverse("wagtailsettings:edit", args=("cms", "footersettings", site.pk))


def _post(admin: Client, title: str, label: str, url: str, *, saved: bool = False) -> Any:
    """Post one column and one profile; `saved` posts them as the rows already stored."""
    ids = (
        {
            "column": {"id": FooterColumn.objects.get().pk},
            "profile": {"id": SocialProfile.objects.get().pk},
        }
        if saved
        else {"column": {}, "profile": {}}
    )
    link = {"page": "", "url": url, "label": label}
    column = {
        "language": primary_language(),
        "title": title,
        "links": streamfield([("external", link)]),
        **ids["column"],
    }
    data = nested_form_data(
        {
            "columns": inline_formset([column], initial=int(saved)),
            "profiles": inline_formset(
                [{"network": "mastodon", "url": URL, "language": "", **ids["profile"]}],
                initial=int(saved),
            ),
        }
    )
    return admin.post(_edit_url(), data)


def test_the_footer_forms_are_nfc() -> None:
    assert issubclass(get_edit_handler(FooterSettings).get_form_class(), NFCFormMixin)
    formsets = get_edit_handler(FooterSettings).get_form_class().formsets
    for name in ("columns", "profiles"):
        assert issubclass(formsets[name].form, NFCFormMixin)


def test_the_real_settings_form_stores_nfc_text_and_exact_urls(admin: Client) -> None:
    response = _post(admin, DECOMPOSED, DECOMPOSED, URL)
    assert response.status_code == 302, getattr(response, "context", None)
    column = FooterColumn.objects.get()
    assert column.title == COMPOSED
    raw = column.links.stream_block.get_prep_value(column.links)
    assert raw[0]["value"]["label"] == COMPOSED  # a StreamField block
    assert raw[0]["value"]["url"] == URL  # a URL stays byte-exact
    profile = SocialProfile.objects.get()
    assert profile.url == URL
    assert profile.network == "mastodon"


def _snapshot() -> tuple[Any, ...]:
    column = FooterColumn.objects.get()
    links = [block["value"] for block in column.links.stream_block.get_prep_value(column.links)]
    profile = SocialProfile.objects.get()
    return (column.pk, column.title, links, profile.pk, profile.url, profile.network)


def test_an_nfc_save_changes_nothing(admin: Client) -> None:
    assert _post(admin, COMPOSED, COMPOSED, URL).status_code == 302
    before = _snapshot()
    assert _post(admin, COMPOSED, COMPOSED, URL, saved=True).status_code == 302
    assert _snapshot() == before

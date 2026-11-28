"""I18N.10: CMS page admin forms and StreamField blocks store text in Unicode NFC."""

import unicodedata
from typing import Any

import pytest
from django import forms
from django.test import Client
from django.urls import reverse
from wagtail.admin.panels import get_edit_handler
from wagtail.test.utils.form_data import nested_form_data, rich_text, streamfield

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import (
    BlogAuthor,
    BlogIndexPage,
    BlogPostPage,
    FormPage,
    HomePage,
    LegalPage,
    StandardPage,
    TermLandingPage,
    VendorLandingPage,
    VendorRecruitmentPage,
)
from apps.cms.tests.factories import HomePageFactory, StandardPageFactory
from apps.core.nfc_forms import NFCFormMixin, nfc_block_data, nfc_stream_value

pytestmark = pytest.mark.django_db

COMPOSED = "Café à la carte"
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)
URL = "https://example.org/café?q=é"


@pytest.mark.parametrize(
    "model",
    [
        HomePage,
        StandardPage,
        LegalPage,
        VendorRecruitmentPage,
        BlogIndexPage,
        BlogPostPage,
        TermLandingPage,
        VendorLandingPage,
        FormPage,
    ],
)
def test_every_page_type_has_the_nfc_form(model: Any) -> None:
    form_class = get_edit_handler(model).get_form_class()
    assert issubclass(form_class, NFCFormMixin)


def _body_block() -> Any:
    return StandardPage._meta.get_field("body").stream_block


@pytest.fixture
def admin(client: Client) -> Client:
    verified_login(client, UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _data(title: str, slug: str, blocks_data: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = nested_form_data(
        {"title": title, "slug": slug, "body": streamfield(blocks_data), "action-publish": "1"}
    )
    return data


def _add_url(parent: Any) -> str:
    return reverse("wagtailadmin_pages:add", args=("cms", "standardpage", parent.pk))


def _cta(heading: str, url: str) -> tuple[str, Any]:
    link = {"page": "", "url": url, "label": DECOMPOSED}
    return ("cta", {"heading": heading, "text": "", "link": link})


def _faq() -> tuple[str, Any]:
    item = {
        "deleted": "",
        "order": "0",
        "id": "",
        "value": {"question": DECOMPOSED, "answer": rich_text(f"<p>{DECOMPOSED}</p>")},
    }
    return ("faq", {"heading": DECOMPOSED, "items": {"count": "1", "0": item}})


def test_the_real_admin_form_stores_nfc_in_title_stream_list_and_struct(admin: Client) -> None:
    parent = HomePageFactory.create()
    data = _data(DECOMPOSED, "nfc-page", [_cta(DECOMPOSED, URL), _faq()])
    response = admin.post(_add_url(parent), data)
    assert response.status_code == 302, getattr(response, "context", None)
    page = StandardPage.objects.get(slug="nfc-page")
    assert page.title == COMPOSED
    raw = _body_block().get_prep_value(page.body)
    cta, faq = raw[0]["value"], raw[1]["value"]
    assert cta["heading"] == COMPOSED  # a StructBlock child
    assert cta["link"]["label"] == COMPOSED  # a nested StructBlock
    assert cta["link"]["url"] == URL  # a URL stays byte-exact
    assert faq["heading"] == COMPOSED
    item = faq["items"][0]["value"]  # a ListBlock of StructBlocks
    assert item["question"] == COMPOSED
    assert COMPOSED in item["answer"]
    assert DECOMPOSED not in item["answer"]


def test_a_decomposed_slug_clashes_with_its_composed_sibling(admin: Client) -> None:
    parent = HomePageFactory.create()
    composed = "caf\u00e9"
    StandardPageFactory.create(parent=parent, title="Sibling", slug=composed)
    data = _data(DECOMPOSED, unicodedata.normalize("NFD", composed), [])
    response = admin.post(_add_url(parent), data)
    assert response.status_code == 200
    assert "slug" in response.context["form"].errors
    assert not StandardPage.objects.filter(title=COMPOSED).exists()


def test_normalisation_runs_before_the_form_clean() -> None:
    seen: list[str] = []

    class Plain(NFCFormMixin, forms.Form):
        title = forms.CharField()

        def clean(self) -> dict[str, Any] | None:
            seen.append(self.cleaned_data["title"])
            return super().clean()

    form = Plain({"title": DECOMPOSED})
    assert form.is_valid()
    assert seen == [COMPOSED]


def test_an_nfc_edit_changes_nothing_and_a_decomposed_edit_makes_one_revision(
    admin: Client,
) -> None:
    parent = HomePageFactory.create()
    para = ("paragraph", rich_text(f"<p>{COMPOSED}</p>"))
    assert admin.post(_add_url(parent), _data(COMPOSED, "same", [para])).status_code == 302
    page = StandardPage.objects.get(slug="same")
    edit_url = reverse("wagtailadmin_pages:edit", args=(page.pk,))
    before = page.revisions.count()

    assert admin.post(edit_url, _data(COMPOSED, "same", [para])).status_code == 302
    page.refresh_from_db()
    assert page.title == COMPOSED
    assert COMPOSED in str(_body_block().get_prep_value(page.body))
    assert page.revisions.count() == before + 1

    decomposed = ("paragraph", rich_text(f"<p>{DECOMPOSED}</p>"))
    assert admin.post(edit_url, _data(DECOMPOSED, "same", [decomposed])).status_code == 302
    page.refresh_from_db()
    assert page.title == COMPOSED
    body = str(_body_block().get_prep_value(page.body))
    assert COMPOSED in body
    assert DECOMPOSED not in body
    latest = page.get_latest_revision()
    assert latest.content["title"] == COMPOSED
    assert DECOMPOSED not in latest.content["body"]
    assert page.revisions.count() == before + 2  # one revision per save, none extra


def test_unchanged_stream_value_is_returned_as_is() -> None:
    value = _body_block().to_python([{"type": "paragraph", "value": f"<p>{COMPOSED}</p>"}])
    assert nfc_stream_value(value) is value


def test_a_url_and_ids_in_a_block_stay_byte_exact() -> None:
    raw = [
        {
            "type": "cta",
            "id": "abc",
            "value": {"heading": DECOMPOSED, "link": {"url": URL, "label": DECOMPOSED}},
        }
    ]
    out = nfc_block_data(_body_block(), raw)
    assert out[0]["id"] == "abc"
    assert out[0]["value"]["heading"] == COMPOSED
    assert out[0]["value"]["link"]["url"] == URL


def test_snippet_stream_fields_are_nfc() -> None:
    form_class = get_edit_handler(BlogAuthor).get_form_class()
    assert issubclass(form_class, NFCFormMixin)


def test_no_page_model_uses_a_plain_inline_panel() -> None:
    """Every InlinePanel on a page or snippet model is an NFCInlinePanel (I18N.10)."""
    from wagtail.admin.panels import InlinePanel

    def panels(node: Any) -> Any:
        yield node
        for child in getattr(node, "children", []):
            yield from panels(child)

    for model in (FormPage, HomePage, StandardPage):
        for panel in panels(get_edit_handler(model)):
            if isinstance(panel, InlinePanel):
                assert type(panel).__name__ == "NFCInlinePanel", (model, panel.relation_name)

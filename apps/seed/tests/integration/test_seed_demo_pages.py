"""THM.23 2/3: the demo seed gives a rich About page, a Contact page, the contact points, the
fourth menu entry and an image for every product; a second run changes nothing.

Language-agnostic: the primary language and the seed's own texts, never 'en' or a fixed slug.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale

from apps.catalog.models import Product
from apps.cms.models import BlogIndexPage, HomePage, NavigationMenu, StandardPage
from apps.cms.seed import seed_text
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import MediaImage, PlatformOperator
from apps.seed import data, demo_pages
from apps.seed.services import TEST_CONTEXT, reset, seed

pytestmark = pytest.mark.django_db


def _home() -> HomePage:
    home: HomePage = HomePage.objects.get(locale=Locale.get_default(), depth=2)
    return home


def about() -> StandardPage:
    page: StandardPage = StandardPage.objects.child_of(_home()).get(
        slug=seed_text(primary_language()).about.slug
    )
    return page


def contact() -> StandardPage:
    slug = demo_pages.CONTACT.slug.get(primary_language()) or demo_pages.CONTACT.slug["en"]
    page: StandardPage = StandardPage.objects.child_of(_home()).get(slug=slug)
    return page


def test_the_seeded_pages_menu_and_images(demo_seed: ChangeSet, client: Client) -> None:
    """One seed, several read-only checks (a seed takes a while)."""
    page = about()
    assert client.get(page.url).status_code == 200
    assert {"figure", "counter", "video_modal"} <= {block.block_type for block in page.body}
    assert page.body[0].value.source == seed_text(primary_language()).about.body

    contact_page = contact()
    response = client.get(contact_page.url)
    assert response.status_code == 200
    assert demo_pages.SUPPORT_EMAIL in response.content.decode()
    assert client.get(reverse("dsa_contact_points")).status_code == 200

    menu = NavigationMenu.objects.get(locale=Locale.get_default(), location="header")
    pages = [link.value["page"].specific for link in menu.links]
    assert len(pages) == 4
    assert isinstance(pages[0], StandardPage)
    assert isinstance(pages[1], BlogIndexPage)
    assert pages[2].pk == page.pk
    assert pages[3].pk == contact_page.pk
    for target in pages:
        assert client.get(target.url).status_code == 200

    assert Product.objects.exists()
    for product in Product.objects.all():
        assert product.images.filter(is_cover=True).count() == 1, product.sku

    gallery = {p.pk: list(p.images.values_list("pk", flat=True)) for p in Product.objects.all()}
    assert seed(ctx=TEST_CONTEXT).is_empty  # a second run changes nothing
    assert gallery == {
        p.pk: list(p.images.values_list("pk", flat=True)) for p in Product.objects.all()
    }


def test_an_edited_about_page_is_left_alone(demo_seed: ChangeSet) -> None:
    page = about()
    page.body = [("paragraph", "<p>Edited by a person.</p>")]
    page.save_revision().publish()
    seed(ctx=TEST_CONTEXT)
    assert [block.block_type for block in about().body] == ["paragraph"]


def test_an_about_body_that_differs_by_one_character_is_left_alone(demo_seed: ChangeSet) -> None:
    page = about()
    page.body = [("paragraph", seed_text(primary_language()).about.body + " ")]
    page.save_revision().publish()
    seed(ctx=TEST_CONTEXT)
    assert [block.block_type for block in about().body] == ["paragraph"]


def test_reset_removes_the_generated_product_images(demo_seed: ChangeSet) -> None:
    prefix = data.PRODUCT_IMAGE_PREFIX
    assert MediaImage.objects.filter(title__startswith=prefix).exists()
    reset(ctx=TEST_CONTEXT)
    assert not MediaImage.objects.filter(title__startswith=prefix).exists()


def test_the_operator_contact_languages_are_configured_languages(demo_seed: ChangeSet) -> None:
    operator = PlatformOperator.objects.get()
    for codes in (operator.authority_contact_languages, operator.user_contact_languages):
        assert codes
        assert set(codes) <= set(configured_languages())


def test_the_contact_languages_follow_the_configured_languages(settings: Any) -> None:
    settings.LANGUAGES = [("it", "Italian"), ("de", "German")]
    settings.LANGUAGE_CODE = "it"
    assert demo_pages.contact_languages() == ["it", "de"]
    settings.LANGUAGES = [("fr", "French"), ("es", "Spanish")]
    settings.LANGUAGE_CODE = "fr"
    assert demo_pages.contact_languages() == ["fr"]  # no text of its own: the primary one only


def test_a_product_whose_image_an_editor_removed_gets_no_new_one(demo_seed: ChangeSet) -> None:
    product = Product.objects.first()
    assert product is not None
    product.images.all().delete()
    seed(ctx=TEST_CONTEXT)
    assert not product.images.exists()


def test_reset_keeps_a_contact_page_an_editor_wrote(demo_seed: ChangeSet) -> None:
    page = contact()
    page.body = [("paragraph", "<p>Written by an editor.</p>")]
    page.save_revision().publish()
    reset(ctx=TEST_CONTEXT)
    assert StandardPage.objects.filter(pk=page.pk).exists()

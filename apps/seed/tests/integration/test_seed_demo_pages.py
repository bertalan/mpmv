"""THM.23 2/3: the demo seed gives a rich About page, a Contact page, the contact points, the
fourth menu entry and an image for every product; a second run changes nothing.

Language-agnostic: the primary language and the seed's own texts, never 'en' or a fixed slug.
"""

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale

from apps.catalog.models import Product
from apps.cms.models import HomePage, NavigationMenu, StandardPage
from apps.cms.seed import seed_text
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.core.models import MediaImage
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


def test_the_about_page_is_reachable_and_rich(demo_seed: ChangeSet, client: Client) -> None:
    page = about()
    response = client.get(page.url)
    assert response.status_code == 200
    kinds = [block.block_type for block in page.body]
    assert {"figure", "counter", "video_modal"} <= set(kinds)
    assert page.body[0].value.source == seed_text(primary_language()).about.body


def test_the_contact_page_is_reachable_with_its_points(
    demo_seed: ChangeSet, client: Client
) -> None:
    page = contact()
    response = client.get(page.url)
    assert response.status_code == 200
    assert demo_pages.SUPPORT_EMAIL in response.content.decode()
    assert client.get(reverse("dsa_contact_points")).status_code == 200


def test_the_header_menu_has_the_four_entries_and_each_is_reachable(
    demo_seed: ChangeSet, client: Client
) -> None:
    menu = NavigationMenu.objects.get(locale=Locale.get_default(), location="header")
    pages = [link.value["page"].specific for link in menu.links]
    assert [type(page) for page in pages] == [
        StandardPage,
        type(pages[1]),
        StandardPage,
        StandardPage,
    ]
    assert len(pages) == 4
    assert pages[2].pk == about().pk
    assert pages[3].pk == contact().pk
    for page in pages:
        assert client.get(page.url).status_code == 200


def test_every_seeded_product_has_an_image_and_a_cover(demo_seed: ChangeSet) -> None:
    products = Product.objects.all()
    assert products.count() >= 1
    for product in products:
        assert product.images.exists(), product.sku
        assert product.images.filter(is_cover=True).count() == 1, product.sku


def test_a_second_run_changes_nothing(demo_seed: ChangeSet) -> None:
    assert seed(ctx=TEST_CONTEXT).is_empty


def test_an_edited_about_page_is_left_alone(demo_seed: ChangeSet) -> None:
    page = about()
    page.body = [("paragraph", "<p>Edited by a person.</p>")]
    page.save_revision().publish()
    seed(ctx=TEST_CONTEXT)
    assert [block.block_type for block in about().body] == ["paragraph"]


def test_a_product_with_an_image_keeps_its_gallery(demo_seed: ChangeSet) -> None:
    product = Product.objects.first()
    assert product is not None
    before = list(product.images.values_list("pk", flat=True))
    seed(ctx=TEST_CONTEXT)
    assert list(product.images.values_list("pk", flat=True)) == before


def test_reset_removes_the_generated_product_images(demo_seed: ChangeSet) -> None:
    prefix = data.PRODUCT_IMAGE_PREFIX
    assert MediaImage.objects.filter(title__startswith=prefix).exists()
    reset(ctx=TEST_CONTEXT)
    assert not MediaImage.objects.filter(title__startswith=prefix).exists()

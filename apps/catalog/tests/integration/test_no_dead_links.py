"""No listing links to a product page that does not exist yet (the CAT.7 page is not routed).

With `PRODUCT_PAGES_ROUTED` false (the production default until CAT.7) a product card shows its
title without a link; every `/product/<slug>/` link a page renders would answer 404.
"""

from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PRODUCT_PREFIXES = ("/product/", "/it/prodotto/")


def _shop() -> Any:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    for language, slug in (("en", "steel-bag"), ("it", "borsa-acciaio")):
        ProductTranslationFactory.create(
            product=product,
            language=language,
            title=slug,
            slug=slug,
            status=TranslationStatus.PUBLISHED,
        )
    return vendor


def _product_links(client: Client, path: str) -> tuple[list[str], str]:
    response = client.get(path)
    assert response.status_code == 200
    page = BeautifulSoup(response.content, "html.parser")
    links = [str(a["href"]) for a in page.find_all("a", href=True)]
    return [h for h in links if h.startswith(PRODUCT_PREFIXES)], page.get_text()


@pytest.mark.parametrize("path", ["/shop/acme/", "/it/shop/acme/"])
def test_no_page_links_to_a_missing_product_page(
    client: Client, settings: SettingsWrapper, path: str
) -> None:
    settings.PRODUCT_PAGES_ROUTED = False
    _shop()
    links, text = _product_links(client, path)
    assert links == []
    assert "steel-bag" in text or "borsa-acciaio" in text  # the card is still shown


def test_the_cards_link_to_the_product_page_once_it_is_routed(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.PRODUCT_PAGES_ROUTED = True
    _shop()
    links, _ = _product_links(client, "/shop/acme/")
    assert links == ["/product/steel-bag/"]

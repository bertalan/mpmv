"""CAT.22: sizes in mm, material, colour and country of origin in the specification table.

Done when: the fields show in the table in the primary and a second language, in both themes;
only filled values show; a product without them adds no row.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import localized, other_language

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(params=["base", "ride"])
def theme(request: Any, settings: SettingsWrapper) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def make_product(**fields: Any) -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    fields.setdefault("weight_kg", None)
    fields.setdefault("length_cm", None)
    fields.setdefault("width_cm", None)
    fields.setdefault("height_cm", None)
    return ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("18.00"), **fields
    )


def translate(product: Product, language: str, **fields: Any) -> None:
    ProductTranslationFactory.create(
        product=product,
        language=language,
        title=f"Mug {language}",
        slug=f"mug-{language}",
        status=PUBLISHED,
        **fields,
    )


def specs(client: Client, language: str) -> dict[str, str]:
    response = client.get(localized("/product/mug-" + language + "/", language))
    assert response.status_code == 200
    page = BeautifulSoup(response.content, "html.parser")
    return {
        dt.get_text(strip=True): dt.find_next_sibling("dd").get_text(" ", strip=True)  # type: ignore[union-attr]
        for dt in page.select(".product-specs dt")
    }


def test_the_fields_show_in_each_language(client: Client, theme: str) -> None:
    product = make_product(length_mm=300, width_mm=200, height_mm=100, country_of_origin="IT")
    translate(product, primary_language(), material="Glazed ceramic", colour="Matt black")
    translate(product, other_language(), material="Ceramica smaltata", colour="Nero opaco")
    first = specs(client, primary_language())
    assert first["Length"] == "300 mm"
    assert first["Width"] == "200 mm"
    assert first["Height"] == "100 mm"
    assert first["Material"] == "Glazed ceramic"
    assert first["Colour"] == "Matt black"
    assert first["Country of origin"] == "IT"
    second = specs(client, other_language())
    assert second["Material"] == "Ceramica smaltata"
    assert second["Colour"] == "Nero opaco"
    assert second["Length"] == "300 mm"


def test_absent_values_render_nothing(client: Client, theme: str) -> None:
    product = make_product(width_mm=200)
    translate(product, primary_language(), material="Glazed ceramic")
    rows = specs(client, primary_language())
    assert rows == {"Width": "200 mm", "Material": "Glazed ceramic"}


def test_a_product_without_the_data_has_no_specification_table(client: Client, theme: str) -> None:
    product = make_product()
    translate(product, primary_language())
    assert specs(client, primary_language()) == {}

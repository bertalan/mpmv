"""I18N.20: the Fits tab of the product page and the Fits pages of the shop space show no English
UI text under it, de, fr and es. Data names (makes, models, notes) are data and stay as entered."""

from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import translation
from psycopg.types.range import Range
from wagtail.models import Site

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.fitment.models import ProductFitmentState
from apps.fitment.tests.factories import (
    FitmentNodeFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.integration.test_product_specs import make_product

pytestmark = pytest.mark.django_db

LANGUAGES = ["it", "de", "fr", "es"]
STOREFRONT_UI = ["Fits", "Not for"]
MANAGER_UI = ["Specific items", "Fits everything", "Not declared"]


def _tr(language: str, text: str) -> str:
    with translation.override(language):
        return translation.gettext(text)


@pytest.fixture(autouse=True)
def _indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(params=["base", "ride"])
def theme(request: Any, settings: Any) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def _page(client: Client, product: Any, language: str) -> str:
    """The product page in `language`: a translation per language, then the localized URL."""
    from apps.catalog.tests.factories import ProductTranslationFactory

    for code in LANGUAGES:
        ProductTranslationFactory.create(
            product=product,
            language=code,
            title=f"Mug {code}",
            slug=f"mug-{code}",
            status="published",
        )
    from django.conf import settings

    from apps.catalog.slugs import product_path

    url = product_path(language, f"mug-{language}", default_language=settings.LANGUAGE_CODE)
    return _text(client, url)


def _text(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser").get_text(" ", strip=True)


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_storefront_fits_tab_is_translated(client: Client, theme: str, language: str) -> None:
    product = make_product()
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.SPECIFIC)
    make = FitmentNodeFactory.create(name="Brand")
    for n in range(30):
        node = FitmentNodeFactory.create(parent=make, name=f"Roadster {n}")
        ProductFitmentFactory.create(product=product, node=node, years=Range(2015, 2021))
    ProductFitmentFactory.create(product=product, node=make, is_exclusion=True)
    text = _page(client, product, language)
    for msgid in STOREFRONT_UI:
        translated = _tr(language, msgid)
        assert translated != msgid or language == "x"
        assert translated in text
    assert "Show " not in text  # "Show N more" is translated
    assert "Not for Brand" not in text


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_universal_text_is_translated(client: Client, theme: str, language: str) -> None:
    product = make_product()
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.UNIVERSAL)
    text = _page(client, product, language)
    assert _tr(language, "Fits everything.") in text
    assert "Fits everything" not in text


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_shop_fits_pages_are_translated(client: Client, language: str) -> None:
    vendor = VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    User.objects.filter(pk=owner.pk).update(preferred_language=language)
    from apps.catalog.tests.factories import ProductFactory

    product = ProductFactory.create(vendor=vendor)
    text = _text(client, reverse("manager:shop:fitment"))
    text += _text(client, reverse("manager:shop:fitment_product", args=[product.public_id]))
    for msgid in MANAGER_UI:
        assert _tr(language, msgid) != msgid
        assert msgid not in text

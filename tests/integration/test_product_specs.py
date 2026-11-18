"""THM.7 part 3: specifications and fitment on the product page, in both themes.

Done when: the product's attributes, weight, dimensions, GTIN and MPN show as a specifications
list, and the fitment rows (FIT.2) as a summary grouped by make; both are absent when the product
has no such data; `base` and `ride` show the same data; a long fitment list is capped with a
`<details>` that works without JavaScript; the fitment summary costs a fixed number of queries.
There is no FAQ on a product (no model field): nothing is rendered for it.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from psycopg.types.range import Range
from pytest_django.fixtures import Settings as SettingsWrapper
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    AttributeFactory,
    AttributeTranslationFactory,
    AttributeValueFactory,
    AttributeValueTranslationFactory,
    ProductAttributeFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.core.models import TranslationStatus
from apps.fitment.models import FitmentNote, ProductFitmentState
from apps.fitment.tests.factories import (
    FitmentNodeFactory,
    FitmentNoteTranslationFactory,
    ProductFitmentFactory,
    ProductFitmentStateFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
THEMES = ["base", "ride"]
PATH = "/product/hand-made-mug/"


@pytest.fixture(autouse=True)
def _indexing() -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture(params=THEMES)
def theme(request: Any, settings: SettingsWrapper) -> str:
    settings.MPMV_THEME_DEFAULT = request.param
    return str(request.param)


def make_product(**fields: Any) -> Product:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True)
    fields.setdefault("weight_kg", None)  # the factory gives every product a weight
    product = ProductFactory.create(
        vendor=vendor, status=Product.Status.PUBLISHED, price_net=Decimal("18.00"), **fields
    )
    ProductTranslationFactory.create(
        product=product, title="Hand-made mug", slug="hand-made-mug", status=PUBLISHED
    )
    return product


def soup(client: Client) -> Any:
    response = client.get(PATH)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def specs(page: Any) -> list[tuple[str, str]]:
    return [
        (dt.get_text(strip=True), dt.find_next_sibling("dd").get_text(" ", strip=True))
        for dt in page.select(".product-specs dt")
    ]


def fitment(page: Any) -> list[tuple[str, str]]:
    return [
        (group.find(["h3", "h4"]).get_text(strip=True), li.get_text(" ", strip=True))
        for group in page.select(".product-fitment .fitment-group")
        for li in group.select("li")
    ]


def add_specs(product: Product) -> None:
    colour = AttributeFactory.create(code="finish")
    AttributeTranslationFactory.create(
        attribute=colour, language="en", status=PUBLISHED, name="Finish"
    )
    glaze = AttributeValueFactory.create(attribute=colour, value="glaze")
    AttributeValueTranslationFactory.create(
        value=glaze, language="en", status=PUBLISHED, label="Glazed"
    )
    ProductAttributeFactory.create(product=product, attribute=colour, position=1).values.set(
        [glaze]
    )
    width = AttributeFactory.create(code="width", kind="number", unit="cm")
    AttributeTranslationFactory.create(
        attribute=width, language="en", status=PUBLISHED, name="Width"
    )
    ProductAttributeFactory.create(
        product=product, attribute=width, numeric_value=Decimal("8.500"), position=2
    )
    maker = AttributeFactory.create(code="maker", kind="text")
    AttributeTranslationFactory.create(
        attribute=maker, language="en", status=PUBLISHED, name="Made by"
    )
    ProductAttributeFactory.create(product=product, attribute=maker, text_value="Anna", position=3)


def add_fitment(product: Product, models_per_make: int = 2, makes: int = 1) -> None:
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.SPECIFIC)
    for m in range(makes):
        make = FitmentNodeFactory.create(name=f"Make {m}")
        for n in range(models_per_make):
            node = FitmentNodeFactory.create(parent=make, name=f"Model {m}-{n}")
            ProductFitmentFactory.create(product=product, node=node)


@pytest.mark.parametrize("status", ["proposed", "retired"])
def test_only_active_nodes_are_public(client: Client, theme: str, status: str) -> None:
    """Spec 17.2.4: a node that is not active, or sits under one, never shows (merged nodes are
    deleted and their rows moved to the target, so they cannot appear)."""
    product = make_product()
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.SPECIFIC)
    make = FitmentNodeFactory.create(name="Shown make")
    good = FitmentNodeFactory.create(parent=make, name="Shown model")
    bad = FitmentNodeFactory.create(parent=make, name="Hidden model", status=status)
    under_bad = FitmentNodeFactory.create(parent=bad, name="Under hidden")
    hidden_make = FitmentNodeFactory.create(name="Hidden make", status=status)
    under_make = FitmentNodeFactory.create(parent=hidden_make, name="Under hidden make")
    for node in (good, bad, under_bad, under_make):
        ProductFitmentFactory.create(product=product, node=node)
    assert fitment(soup(client)) == [("Shown make", "Shown model")]


def test_a_product_with_only_unapproved_fitment_shows_no_fitment(
    client: Client, theme: str
) -> None:
    product = make_product()
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.SPECIFIC)
    make = FitmentNodeFactory.create(name="M")
    node = FitmentNodeFactory.create(parent=make, name="N", status="proposed")
    ProductFitmentFactory.create(product=product, node=node)
    assert soup(client).select_one(".product-fitment") is None


def test_specifications_show_the_product_data(client: Client, theme: str) -> None:
    product = make_product(
        weight_kg=Decimal("0.350"), gtin="4006381333931", mpn="MUG-1", length_cm=Decimal("10")
    )
    add_specs(product)
    rows = dict(specs(soup(client)))
    assert rows["Finish"] == "Glazed"
    assert rows["Width"] == "8.5 cm"
    assert rows["Made by"] == "Anna"
    assert rows["Weight"] == "0.35 kg"
    assert rows["GTIN"] == "4006381333931"
    assert rows["MPN"] == "MUG-1"


def test_specifications_are_absent_without_data(client: Client, theme: str) -> None:
    make_product()
    page = soup(client)
    assert page.select_one(".product-specs") is None
    assert "Specifications" not in page.get_text()


def test_variant_axes_are_not_specifications(client: Client, theme: str) -> None:
    product = make_product()
    axis = AttributeFactory.create(code="size")
    AttributeTranslationFactory.create(attribute=axis, language="en", status=PUBLISHED, name="Size")
    ProductAttributeFactory.create(product=product, attribute=axis, is_variant_axis=True)
    assert soup(client).select_one(".product-specs") is None


def test_fitment_is_grouped_by_make(client: Client, theme: str) -> None:
    product = make_product()
    add_fitment(product, models_per_make=2, makes=2)
    found = fitment(soup(client))
    assert [(make, text) for make, text in found] == [
        ("Make 0", "Model 0-0"),
        ("Make 0", "Model 0-1"),
        ("Make 1", "Model 1-0"),
        ("Make 1", "Model 1-1"),
    ]


def test_fitment_shows_years_notes_and_exclusions(client: Client, theme: str) -> None:
    product = make_product()
    ProductFitmentStateFactory.create(product=product)
    make = FitmentNodeFactory.create(name="Brand")
    model = FitmentNodeFactory.create(parent=make, name="Roadster")
    note = FitmentNote.objects.create(code="rear")
    FitmentNoteTranslationFactory.create(note=note, language="en", text="Rear only")
    ProductFitmentFactory.create(
        product=product,
        node=model,
        years=Range(2015, 2021),
        note=note,  # [2015, 2021)
    )
    ProductFitmentFactory.create(product=product, node=make, is_exclusion=True)
    texts = [text for _make, text in fitment(soup(client))]
    assert any("Roadster" in t and "2015" in t and "2020" in t and "Rear only" in t for t in texts)
    assert any("Not for" in t for t in texts)


def test_a_universal_product_says_so_without_a_list(client: Client, theme: str) -> None:
    product = make_product()
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.UNIVERSAL)
    page = soup(client)
    box = page.select_one(".product-fitment")
    assert box is not None
    assert box.select_one("li") is None
    assert "Fits everything" in box.get_text()


def test_fitment_is_absent_when_undeclared_or_empty(client: Client, theme: str) -> None:
    product = make_product()
    assert soup(client).select_one(".product-fitment") is None
    ProductFitmentStateFactory.create(product=product, mode=ProductFitmentState.Mode.UNDECLARED)
    assert soup(client).select_one(".product-fitment") is None


def test_a_long_fitment_list_is_capped_with_details(client: Client, theme: str) -> None:
    product = make_product()
    add_fitment(product, models_per_make=30)
    page = soup(client)
    details = page.select_one(".product-fitment details")
    assert details is not None
    assert details.select_one("summary") is not None
    inside = len(details.select("li"))
    outside = len(page.select(".product-fitment li")) - inside
    assert outside == 12
    assert inside == 18
    assert page.select_one(".product-fitment [style]") is None


def test_a_short_fitment_list_has_no_details(client: Client, theme: str) -> None:
    add_fitment(make_product(), models_per_make=3)
    assert soup(client).select_one(".product-fitment details") is None


def test_base_and_ride_show_the_same_data(client: Client, settings: SettingsWrapper) -> None:
    product = make_product(weight_kg=Decimal("1.2"), mpn="M-9")
    add_specs(product)
    add_fitment(product, models_per_make=20, makes=2)
    shown = {}
    for name in THEMES:
        settings.MPMV_THEME_DEFAULT = name
        page = soup(client)
        shown[name] = (specs(page), fitment(page))
    assert shown["base"] == shown["ride"]
    assert shown["base"][0]
    assert shown["base"][1]


def test_ride_puts_them_in_tabs_and_keeps_every_panel_in_the_markup(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    product = make_product()
    add_specs(product)
    add_fitment(product)
    page = soup(client)
    tabs = {tab["data-bs-target"].lstrip("#") for tab in page.select("[role=tab]")}
    assert {"product-panel-specs", "product-panel-fitment"} <= tabs
    for panel_id in tabs:
        assert page.find(id=panel_id)["role"] == "tabpanel"
    assert page.select_one("#product-panel-specs .product-specs") is not None
    assert page.select_one("#product-panel-fitment .product-fitment") is not None


def test_ride_has_only_the_tabs_that_have_data(client: Client, settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    make_product()
    page = soup(client)
    assert page.find(id="product-panel-specs") is None
    assert page.find(id="product-panel-fitment") is None
    assert page.find(id="product-panel-details") is not None


def test_there_is_no_faq_without_data(client: Client, theme: str) -> None:
    make_product()
    page = soup(client)
    assert page.select_one(".product-faq") is None


def test_fitment_costs_a_fixed_number_of_queries(
    client: Client, theme: str, django_assert_max_num_queries: Any
) -> None:
    few = make_product()
    add_fitment(few, models_per_make=2)
    client.get(PATH)  # warm caches (content types, site, languages)
    with CaptureQueriesContext(connection) as small:
        client.get(PATH)
    ProductFitmentState.objects.all().delete()
    add_fitment(few, models_per_make=40, makes=3)
    with django_assert_max_num_queries(len(small) + 1):
        assert client.get(PATH).status_code == 200

"""CAT.22: product physical and origin data (sizes in mm, material, colour, country of origin).

Done when: a size of 0 or below and a lowercase or unknown country code are refused; the change
is audited with before and after; the import (`create_product`) accepts and validates the
fields. The specification table is tested in `tests/integration/test_product_physical_specs.py`.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog import engine
from apps.catalog.models import Product
from apps.catalog.services import (
    create_product,
    record_translation_facts_change,
    save_product,
    translation_facts_snapshot,
)
from apps.catalog.tests.factories import ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
SIZES = ("length_mm", "width_mm", "height_mm")


@pytest.mark.parametrize("field", SIZES)
@pytest.mark.parametrize("value", [0, -5])
def test_a_size_of_zero_or_below_is_refused(field: str, value: int) -> None:
    product = ProductFactory.build(**{field: value}, vendor=VendorFactory.create())
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert field in exc.value.message_dict
    with pytest.raises(ValidationError) as saved:
        save_product(product, ctx=CTX)
    assert field in saved.value.message_dict


@pytest.mark.parametrize("field", SIZES)
def test_the_database_refuses_a_size_of_zero(field: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(**{field: 0})


@pytest.mark.parametrize("code", ["it", "It", "UK", "EL", "XX", "ITA", "I"])
def test_a_lowercase_or_unknown_country_code_is_refused(code: str) -> None:
    product = ProductFactory.build(country_of_origin=code, vendor=VendorFactory.create())
    with pytest.raises(ValidationError) as exc:
        product.full_clean()
    assert "country_of_origin" in exc.value.message_dict


def test_sizes_and_a_country_are_accepted_and_empty_is_allowed() -> None:
    product = ProductFactory.build(
        length_mm=300,
        width_mm=200,
        height_mm=100,
        country_of_origin="IT",
        vendor=VendorFactory.create(),
    )
    product.full_clean()
    ProductFactory.build(
        length_mm=None,
        width_mm=None,
        height_mm=None,
        country_of_origin="",
        vendor=VendorFactory.create(),
    ).full_clean()


@pytest.mark.parametrize(
    ("field", "value"),
    [("length_mm", 300), ("width_mm", 200), ("height_mm", 100), ("country_of_origin", "DE")],
)
def test_a_change_is_audited_with_before_and_after(field: str, value: Any) -> None:
    product = ProductFactory.create()
    setattr(product, field, value)
    save_product(product, ctx=CTX)
    entry = AuditLog.objects.get()
    assert entry.action == "catalog.product.updated"
    assert (entry.after or {})[field] == value
    assert (entry.before or {})[field] != value


def test_a_material_or_colour_change_is_audited() -> None:
    translation = ProductTranslationFactory.create(material="Steel", colour="Grey")
    before = translation_facts_snapshot(translation)
    translation.material = "Ceramic"
    translation.save()
    record_translation_facts_change(translation, before, ctx=CTX)
    entry = AuditLog.objects.get()
    assert entry.target_model == "catalog.producttranslation"
    assert (entry.before or {})["material"] == "Steel"
    assert (entry.after or {})["material"] == "Ceramic"
    assert (entry.after or {})["colour"] == "Grey"


def test_an_unchanged_material_writes_no_audit_row() -> None:
    translation = ProductTranslationFactory.create(material="Steel")
    record_translation_facts_change(translation, translation_facts_snapshot(translation), ctx=CTX)
    assert AuditLog.objects.count() == 0


def _data(**overrides: Any) -> engine.ProductIn:
    data: dict[str, Any] = {
        "sku": "MUG-1",
        "price_net": "10.00",
        "translations": (
            engine.TranslationIn(
                primary_language(), "Mug", material="Glazed ceramic", colour="Matt black"
            ),
        ),
    }
    data.update(overrides)
    data["price_net"] = Decimal(data["price_net"])
    return engine.ProductIn(**data)


def test_the_import_accepts_the_fields() -> None:
    vendor = VendorFactory.create()
    create_product(
        vendor,
        _data(length_mm=300, width_mm=200, height_mm=100, country_of_origin="IT"),
        ctx=CTX,
    )
    product = Product.objects.get(vendor=vendor, sku="MUG-1")
    assert (product.length_mm, product.width_mm, product.height_mm) == (300, 200, 100)
    assert product.country_of_origin == "IT"
    text = product.translations.get()
    assert (text.material, text.colour) == ("Glazed ceramic", "Matt black")


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"length_mm": 0}, "length_mm"),
        ({"width_mm": -1}, "width_mm"),
        ({"height_mm": 0}, "height_mm"),
        ({"country_of_origin": "it"}, "country_of_origin"),
        ({"country_of_origin": "ZZ"}, "country_of_origin"),
    ],
)
def test_the_import_refuses_invalid_values_also_on_dry_run(
    overrides: dict[str, Any], field: str
) -> None:
    vendor = VendorFactory.create()
    for dry_run in (True, False):
        with pytest.raises(ValidationError) as exc:
            create_product(vendor, _data(**overrides), ctx=CTX, dry_run=dry_run)
        assert field in exc.value.message_dict

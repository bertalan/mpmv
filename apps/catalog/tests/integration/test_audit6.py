"""Catalogue audit chunk 6: M1 duplicate copies axes and options, M2 slugs on create and
duplicate, M3 no 0.00 variant override or sale price (CAT.14, CAT.5, CAT.3)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.tests.factories import UserFactory
from apps.catalog import engine
from apps.catalog.models import Attribute, Product, ProductAttribute, Variant, VariantOption
from apps.catalog.services import (
    create_product,
    duplicate_product_to_vendor,
    set_product_price,
    set_variant_price,
)
from apps.catalog.tests.factories import ProductFactory, VariantFactory, clothing_attributes
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="api:pipeline", source="api", ip="127.0.0.1")


@pytest.fixture
def attributes() -> dict[str, Attribute]:
    return clothing_attributes()


def tee(**overrides: object) -> engine.ProductIn:
    data: dict[str, object] = {
        "sku": "TEE-1",
        "kind": "variable",
        "price_net": Decimal("19.90"),
        "translations": (
            engine.TranslationIn("en", "Plain tee"),
            engine.TranslationIn("it", "Maglia semplice"),
        ),
        "attributes": (
            engine.AttributeIn("size", values=("s", "m"), axis=True),
            engine.AttributeIn("color", values=("red", "blue"), axis=True),
            engine.AttributeIn("material", values=("cotton",)),
            engine.AttributeIn("bag_width", number=Decimal("30.5")),
            engine.AttributeIn("author", text="A. Writer"),
        ),
        "variants": (
            engine.VariantIn("TEE-1-S-RED", {"size": "s", "color": "red"}),
            engine.VariantIn("TEE-1-M-BLUE", {"size": "m", "color": "blue"}),
        ),
    }
    data.update(overrides)
    return engine.ProductIn(**data)  # type: ignore[arg-type]


def attribute_facts(product: Product) -> list[tuple[object, ...]]:
    return [
        (
            row.attribute.code,
            sorted(row.values.values_list("value", flat=True)),
            row.numeric_value,
            row.text_value,
            row.is_variant_axis,
            row.position,
        )
        for row in ProductAttribute.objects.filter(product=product).order_by("position")
    ]


def option_facts(product: Product) -> dict[str, dict[str, str]]:
    return {
        v.sku: {o.attribute.code: o.value.value for o in v.variant_options.all()}
        for v in Variant.objects.filter(product=product)
    }


# --- M1


def test_a_duplicate_copies_attributes_axes_and_variant_options(
    attributes: dict[str, Attribute],
) -> None:
    create_product(VendorFactory.create(), tee(), ctx=CTX)
    original = Product.objects.get(sku="TEE-1")
    target = VendorFactory.create()
    changes = duplicate_product_to_vendor(
        original,
        target,
        actor=UserFactory.create(platform_role="catalog"),
        reason="transfer",
        ctx=CTX,
    )
    copy = Product.objects.get(vendor=target)
    assert attribute_facts(copy) == attribute_facts(original)
    assert len(attribute_facts(copy)) == 5
    assert option_facts(copy) == option_facts(original)
    assert all(option_facts(copy).values())
    models = [c.model for c in changes.changes]
    assert models.count("catalog.productattribute") == 5
    assert models.count("catalog.variantoption") == 4


def test_a_dry_run_duplicate_lists_attributes_and_options_without_writing(
    attributes: dict[str, Attribute],
) -> None:
    create_product(VendorFactory.create(), tee(), ctx=CTX)
    original = Product.objects.get(sku="TEE-1")
    rows, options = ProductAttribute.objects.count(), VariantOption.objects.count()
    changes = duplicate_product_to_vendor(
        original,
        VendorFactory.create(),
        actor=UserFactory.create(platform_role="catalog"),
        reason="transfer",
        ctx=CTX,
        dry_run=True,
    )
    models = [c.model for c in changes.changes]
    assert models.count("catalog.productattribute") == 5
    assert models.count("catalog.variantoption") == 4
    assert (ProductAttribute.objects.count(), VariantOption.objects.count()) == (rows, options)


# --- M2


def slugs_of(product: Product) -> dict[str, str]:
    return dict(product.translations.values_list("language", "slug"))


@pytest.mark.parametrize("dry_run", [False, True])
def test_create_product_gives_every_translation_its_slug(
    attributes: dict[str, Attribute], dry_run: bool
) -> None:
    changes = create_product(VendorFactory.create(), tee(), ctx=CTX, dry_run=dry_run)
    entries = [c for c in changes.changes if c.model == "catalog.producttranslation"]
    shown = sorted(str(c.after["slug"]) for c in entries if c.after)
    assert shown == ["maglia-semplice", "plain-tee"]
    if not dry_run:
        product = Product.objects.get(sku="TEE-1")
        assert slugs_of(product) == {"en": "plain-tee", "it": "maglia-semplice"}
        created = AuditLog.objects.filter(action="catalog.product.created")
        assert created.filter(target_key=str(product.public_id)).exists()


def test_create_product_suffixes_a_slug_taken_by_another_product(
    attributes: dict[str, Attribute],
) -> None:
    other = ProductFactory.create(languages=["en"])
    other.translations.update(title="Plain tee", slug="plain-tee")
    create_product(VendorFactory.create(), tee(), ctx=CTX)
    assert slugs_of(Product.objects.get(sku="TEE-1"))["en"] == "plain-tee-2"


def test_a_duplicate_regenerates_unique_slugs(attributes: dict[str, Attribute]) -> None:
    create_product(VendorFactory.create(), tee(), ctx=CTX)
    original = Product.objects.get(sku="TEE-1")
    changes = duplicate_product_to_vendor(
        original,
        VendorFactory.create(),
        actor=UserFactory.create(platform_role="catalog"),
        reason="transfer",
        ctx=CTX,
    )
    copy = Product.objects.exclude(pk=original.pk).get()
    assert slugs_of(copy) == {"en": "plain-tee-2", "it": "maglia-semplice-2"}
    entries = [c for c in changes.changes if c.model == "catalog.producttranslation"]
    assert sorted(str(c.after["slug"]) for c in entries if c.after) == [
        "maglia-semplice-2",
        "plain-tee-2",
    ]


# --- M3


@pytest.mark.parametrize("dry_run", [False, True])
def test_create_product_refuses_a_zero_variant_override(
    attributes: dict[str, Attribute], dry_run: bool
) -> None:
    variants = (
        engine.VariantIn(
            "TEE-1-S-RED", {"size": "s", "color": "red"}, price_net_override=Decimal("0.00")
        ),
    )
    with pytest.raises(ValidationError, match=r"override must be above 0\.00"):
        create_product(VendorFactory.create(), tee(variants=variants), ctx=CTX, dry_run=dry_run)
    assert not Product.objects.exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_set_variant_price_refuses_zero(dry_run: bool) -> None:
    variant = VariantFactory.create(price_net_override=Decimal("12.00"))
    with pytest.raises(ValidationError, match=r"override must be above 0\.00"):
        set_variant_price(variant, price_net_override=0, ctx=CTX, dry_run=dry_run)
    variant.refresh_from_db()
    assert variant.price_net_override == Decimal("12.00")


def test_set_variant_price_clears_with_none() -> None:
    variant = VariantFactory.create(price_net_override=Decimal("12.00"))
    set_variant_price(variant, price_net_override=None, ctx=CTX)
    variant.refresh_from_db()
    assert variant.price_net_override is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_set_product_price_refuses_a_zero_sale_price(dry_run: bool) -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    with pytest.raises(ValidationError, match=r"sale price must be above 0\.00"):
        set_product_price(
            product, price_net=Decimal("10.00"), sale_price_net=0, ctx=CTX, dry_run=dry_run
        )


def test_the_database_refuses_a_zero_override() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantFactory.create(price_net_override=Decimal("0.00"))


def test_the_database_refuses_a_zero_sale_price() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory.create(price_net=Decimal("10.00"), sale_price_net=Decimal("0.00"))


def test_the_admin_validation_refuses_a_zero_override_and_sale_price() -> None:
    variant = VariantFactory.create()
    variant.price_net_override = Decimal("0.00")
    with pytest.raises(ValidationError, match="override"):
        variant.full_clean()
    product = ProductFactory.create(price_net=Decimal("10.00"))
    product.sale_price_net = Decimal("0.00")
    with pytest.raises(ValidationError, match="sale price"):
        product.full_clean()

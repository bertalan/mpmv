"""CAT.3: creating a product with typed attributes, variant axes and variants (spec 03 §3.2).

`services.create_product` is the one way the API (and later imports) create a product with its
attributes and variants: validated as a whole, returned as a ChangeSet (dry-run) and audited.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.catalog import engine
from apps.catalog.models import Attribute, Product, ProductAttribute, Variant, VariantOption
from apps.catalog.services import create_product, variant_axes, variant_label
from apps.catalog.tests.factories import ProductFactory, VariantFactory, clothing_attributes
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="api:pipeline", source="api", ip="127.0.0.1")


@pytest.fixture
def attributes() -> dict[str, Attribute]:
    return clothing_attributes()


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


def _variant(sku: str, size: str, color: str, **extra: object) -> engine.VariantIn:
    return engine.VariantIn(sku=sku, options={"size": size, "color": color}, **extra)  # type: ignore[arg-type]


def t_shirt(**overrides: object) -> engine.ProductIn:
    """A variable T-shirt: size x color, given in a scrambled order, plus plain attributes."""
    data: dict[str, object] = {
        "sku": "TEE-1",
        "kind": "variable",
        "price_net": Decimal("19.90"),
        "weight_kg": Decimal("0.200"),
        "translations": (engine.TranslationIn("en", "Plain tee"),),
        "attributes": (
            engine.AttributeIn("size", values=("s", "m"), axis=True),
            engine.AttributeIn("color", values=("red", "blue"), axis=True),
            engine.AttributeIn("material", values=("cotton",)),
            engine.AttributeIn("bag_width", number=Decimal("30.5")),
            engine.AttributeIn("author", text="A. Writer"),
        ),
        "variants": (
            _variant("TEE-1-M-BLUE", "m", "blue"),
            _variant("TEE-1-S-RED", "s", "red", is_default=True),
            _variant("TEE-1-M-RED", "m", "red", price_net_override=Decimal("21.00")),
            _variant("TEE-1-S-BLUE", "s", "blue", stock_qty=4),
        ),
    }
    data.update(overrides)
    return engine.ProductIn(**data)  # type: ignore[arg-type]


def test_a_size_by_color_product_is_created_with_ordered_variants(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    changes = create_product(vendor, t_shirt(), ctx=CTX)

    product = Product.objects.get(vendor=vendor, sku="TEE-1")
    assert product.kind == Product.Kind.VARIABLE
    assert product.price_net == Decimal("19.90")
    assert product.translations.get().title == "Plain tee"
    assert [row.attribute.code for row in variant_axes(product)] == ["size", "color"]
    # Ordered by the axes (size first), then by the values' positions (s < m, red < blue).
    variants = list(product.variants.order_by("position"))
    assert [v.sku for v in variants] == [
        "TEE-1-S-RED",
        "TEE-1-S-BLUE",
        "TEE-1-M-RED",
        "TEE-1-M-BLUE",
    ]
    assert [v.position for v in variants] == [0, 1, 2, 3]
    assert variants[0].is_default
    assert variants[1].stock_qty == 4
    assert variants[2].price_net_override == Decimal("21.00")
    assert {o.attribute.code: o.value.value for o in variants[3].variant_options.all()} == {
        "size": "m",
        "color": "blue",
    }
    assert changes.summary()["create"] == len(changes.changes)
    assert str(product.public_id) in {c.key for c in changes.changes}


def test_plain_attributes_keep_their_type(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    create_product(vendor, t_shirt(), ctx=CTX)
    rows = {row.attribute.code: row for row in ProductAttribute.objects.all()}
    assert list(rows["material"].values.values_list("value", flat=True)) == ["cotton"]
    assert rows["bag_width"].numeric_value == Decimal("30.500")
    assert rows["author"].text_value == "A. Writer"
    assert not rows["material"].is_variant_axis
    assert [rows[c].position for c in ("size", "color", "material", "bag_width", "author")] == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_the_creation_is_audited(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    changes = create_product(vendor, t_shirt(), ctx=CTX)
    entries = AuditLog.objects.filter(action="catalog.product.created")
    assert entries.count() == len(changes.changes)
    product = Product.objects.get()
    entry = entries.get(target_model="catalog.product")
    assert entry.target_key == str(product.public_id)
    assert entry.after["sku"] == "TEE-1"
    assert entry.after["price_net"] == "19.90"


def test_a_dry_run_returns_the_changeset_and_writes_nothing(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    changes = create_product(vendor, t_shirt(), ctx=CTX, dry_run=True)
    models = [c.model for c in changes.changes]
    assert models.count("catalog.product") == 1
    assert models.count("catalog.variant") == 4
    assert models.count("catalog.variantoption") == 8
    assert models.count("catalog.productattribute") == 5
    assert not Product.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_simple_product_cannot_get_variants(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    # Owner decision Q-32.
    with pytest.raises(ValidationError, match="Only a variable product has variants"):
        create_product(vendor, t_shirt(kind="simple"), ctx=CTX)
    assert not Product.objects.exists()


def test_an_unknown_value_is_refused(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    data = t_shirt(attributes=(engine.AttributeIn("size", values=("xxl",), axis=True),))
    with pytest.raises(ValidationError, match="size has no value xxl"):
        create_product(vendor, data, ctx=CTX, dry_run=True)


def test_a_language_must_be_configured(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    data = t_shirt(translations=(engine.TranslationIn("xx", "Tee"),))
    with pytest.raises(ValidationError):
        create_product(vendor, data, ctx=CTX)


def test_a_vendor_cannot_reuse_a_product_sku(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    ProductFactory.create(vendor=vendor, sku="TEE-1")
    with pytest.raises(ValidationError, match="already has a product with this SKU"):
        create_product(vendor, t_shirt(), ctx=CTX)
    create_product(VendorFactory.create(), t_shirt(), ctx=CTX)  # another vendor: fine


def test_a_vendor_cannot_reuse_a_variant_sku(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    VariantFactory.create(product__vendor=vendor, sku="TEE-1-S-RED")
    with pytest.raises(ValidationError, match="already has a variant with this SKU"):
        create_product(vendor, t_shirt(), ctx=CTX)
    assert Product.objects.count() == 1


def test_variant_labels_use_the_translated_values_in_axis_order(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    create_product(vendor, t_shirt(), ctx=CTX)
    variant = Variant.objects.get(sku="TEE-1-M-RED")
    assert variant_label(variant, "en") == "M / Red"
    # Colors are translated; sizes fall back to EN.
    assert variant_label(variant, "it") == "M / Rosso"
    assert VariantOption.objects.filter(variant=variant).count() == 2

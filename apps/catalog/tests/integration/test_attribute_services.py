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
from apps.vendors.engine import VendorCannotWrite
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
    assert entry.after is not None
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


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_zero_price_is_refused_as_a_validation_error(
    attributes: dict[str, Attribute], vendor: Vendor, dry_run: bool
) -> None:
    # CAT.14 (Q-31): a price is never 0.00. The service reports it before the database check.
    data = t_shirt(price_net=Decimal("0.00"))
    with pytest.raises(ValidationError, match=r"cannot be 0\.00"):
        create_product(vendor, data, ctx=CTX, dry_run=dry_run)
    assert not Product.objects.exists()


def test_an_unknown_value_is_refused(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    data = t_shirt(attributes=(engine.AttributeIn("size", values=("xxl",), axis=True),))
    with pytest.raises(ValidationError, match="size has no value xxl"):
        create_product(vendor, data, ctx=CTX, dry_run=True)


def _invalid(data: engine.ProductIn, vendor: Vendor) -> dict[str, list[str]]:
    """The errors of `data`, the same on dry-run and on the real run, which writes nothing."""
    with pytest.raises(ValidationError) as dry:
        create_product(vendor, data, ctx=CTX, dry_run=True)
    with pytest.raises(ValidationError) as real:
        create_product(vendor, data, ctx=CTX)
    assert dry.value.message_dict == real.value.message_dict
    assert not Product.objects.exists()
    return real.value.message_dict


def _with_width(number: Decimal) -> engine.ProductIn:
    """The T-shirt with `number` as its bag width."""
    others = tuple(a for a in t_shirt().attributes if a.code != "bag_width")
    return t_shirt(attributes=(*others, engine.AttributeIn("bag_width", number=number)))


def test_a_number_that_does_not_fit_its_column_is_refused(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    for number, message in [
        ("10000000000", "bag_width has more than 9 digits before the decimal point."),
        ("30.5555", "bag_width has more than 3 decimals."),
    ]:
        data = _with_width(Decimal(number))
        assert message in _invalid(data, vendor)["__all__"]


@pytest.mark.parametrize(
    ("weight", "message"),
    [
        ("-1", "TEE-1-M-BLUE: the weight override cannot be negative."),
        (
            "123456",
            "TEE-1-M-BLUE: the weight override has more than 5 digits before the decimal point.",
        ),
        ("0.0005", "TEE-1-M-BLUE: the weight override has more than 3 decimals."),
    ],
)
def test_a_variant_weight_override_is_validated(
    attributes: dict[str, Attribute], vendor: Vendor, weight: str, message: str
) -> None:
    first, *rest = t_shirt().variants
    variant = engine.VariantIn(first.sku, first.options, weight_kg_override=Decimal(weight))
    errors = _invalid(t_shirt(variants=(variant, *rest)), vendor)
    assert errors["variants"] == [message]


def test_the_product_weight_is_validated(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    errors = _invalid(t_shirt(weight_kg=Decimal("-0.5")), vendor)
    assert errors["weight_kg"] == ["The weight cannot be negative."]


def test_a_blank_sku_is_refused(attributes: dict[str, Attribute], vendor: Vendor) -> None:
    assert _invalid(t_shirt(sku="   "), vendor)["sku"] == ["A SKU cannot be empty."]
    first, *rest = t_shirt().variants
    blank = engine.VariantIn(" ", first.options)
    assert _invalid(t_shirt(variants=(blank, *rest)), vendor)["variants"] == [
        "A variant SKU cannot be empty."
    ]


def test_the_changeset_records_the_stored_number(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    changes = create_product(vendor, _with_width(Decimal("30.5000")), ctx=CTX)
    created = [c.after for c in changes.changes if c.model == "catalog.productattribute"]
    width = next(after for after in created if after and after["attribute"] == "bag_width")
    stored = ProductAttribute.objects.get(attribute__code="bag_width").numeric_value
    assert str(width["numeric_value"]) == str(stored) == "30.500"


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


# --- The vendor SKU namespace (CAT.13): match key, products and variants together


def test_create_product_keeps_the_sku_raw_and_sets_the_match_key(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    create_product(vendor, t_shirt(sku="Tee 1"), ctx=CTX)
    product = Product.objects.get(vendor=vendor)
    assert (product.sku, product.sku_key) == ("Tee 1", "tee 1")
    assert product.staff_ref.startswith("P-")


def test_create_product_refuses_a_product_sku_matching_by_key(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    ProductFactory.create(vendor=vendor, sku="tee-1")
    with pytest.raises(ValidationError, match="already has a product with this SKU"):
        create_product(vendor, t_shirt(), ctx=CTX)
    assert Product.objects.count() == 1


def test_create_product_refuses_a_product_sku_a_variant_holds(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    VariantFactory.create(product__vendor=vendor, sku="Tee-1")
    with pytest.raises(ValidationError, match="already has a variant with this SKU"):
        create_product(vendor, t_shirt(), ctx=CTX)
    assert Product.objects.count() == 1


def test_create_product_refuses_a_variant_sku_a_product_holds(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    ProductFactory.create(vendor=vendor, sku="tee-1-s-red ")
    with pytest.raises(ValidationError, match="already has a product with this SKU: TEE-1-S-RED"):
        create_product(vendor, t_shirt(), ctx=CTX)
    assert Product.objects.count() == 1


def test_create_product_refuses_a_variant_sku_matching_another_variant_by_key(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    VariantFactory.create(product__vendor=vendor, sku="tee-1  s-red")
    data = t_shirt(
        variants=(
            _variant("TEE-1-M-BLUE", "m", "blue"),
            _variant("TEE-1 S-RED", "s", "red"),
            _variant("TEE-1-M-RED", "m", "red"),
            _variant("TEE-1-S-BLUE", "s", "blue"),
        )
    )
    with pytest.raises(ValidationError, match="already has a variant with this SKU: TEE-1 S-RED"):
        create_product(vendor, data, ctx=CTX)
    assert Product.objects.count() == 1


def test_create_product_refuses_a_variant_with_the_product_sku(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    data = t_shirt(
        variants=(
            _variant("tee-1", "m", "blue"),
            _variant("TEE-1-S-RED", "s", "red"),
            _variant("TEE-1-M-RED", "m", "red"),
            _variant("TEE-1-S-BLUE", "s", "blue"),
        )
    )
    with pytest.raises(ValidationError, match="cannot have the SKU of its product"):
        create_product(vendor, data, ctx=CTX)
    assert not Product.objects.exists()


@pytest.mark.parametrize("sku", ["   ", "X" * 64 + "\u00e9"])
def test_create_product_refuses_an_unusable_sku(
    attributes: dict[str, Attribute], vendor: Vendor, sku: str
) -> None:
    with pytest.raises(ValidationError, match="A SKU cannot be"):
        create_product(vendor, t_shirt(sku=sku), ctx=CTX)
    with pytest.raises(ValidationError, match="A SKU cannot be"):
        create_product(vendor, t_shirt(sku=sku), ctx=CTX, dry_run=True)
    assert not Product.objects.exists()


def test_variant_labels_use_the_translated_values_in_axis_order(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    create_product(vendor, t_shirt(), ctx=CTX)
    variant = Variant.objects.get(sku="TEE-1-M-RED")
    assert variant_label(variant, "en") == "M / Red"
    # Colors are translated; sizes fall back to EN.
    assert variant_label(variant, "it") == "M / Rosso"
    assert VariantOption.objects.filter(variant=variant).count() == 2


# --- Writes on behalf of a vendor's API key (CORE.14)


def test_a_write_for_a_vendor_key_needs_the_vendor_active_at_write_time(
    attributes: dict[str, Attribute],
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)  # after auth
    with pytest.raises(VendorCannotWrite):
        create_product(vendor, t_shirt(), ctx=CTX, require_active_vendor=True)
    assert not Product.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_write_for_an_active_vendors_key_goes_through(attributes: dict[str, Attribute]) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    create_product(vendor, t_shirt(), ctx=CTX, require_active_vendor=True)
    assert Product.objects.filter(vendor=vendor).count() == 1


def test_other_writes_do_not_check_the_vendor_status(
    attributes: dict[str, Attribute], vendor: Vendor
) -> None:
    assert vendor.status == Vendor.Status.APPLICANT
    create_product(vendor, t_shirt(), ctx=CTX)
    assert Product.objects.filter(vendor=vendor).count() == 1

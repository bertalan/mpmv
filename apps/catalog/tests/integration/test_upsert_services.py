"""CAT.10: `upsert_products`, `publish` and `retire` (spec 03 §3.2 "Main services" and "Rules")."""

from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog import engine, publishing, services
from apps.catalog.models import Product, ProductTranslation, SlugHistory, Variant
from apps.catalog.tests.factories import (
    ProductFactory,
    SkuAliasFactory,
    VariantFactory,
    clothing_attributes,
)
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="test", source="test")


@pytest.fixture(autouse=True)
def _no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


def item(sku: str = "A-1", **overrides: object) -> engine.ProductIn:
    data: dict[str, object] = {
        "sku": sku,
        "price_net": Decimal("10.00"),
        "weight_kg": Decimal("0.500"),
        "translations": (engine.TranslationIn(primary_language(), f"Title {sku}"),),
    }
    data.update(overrides)
    return engine.ProductIn(**data)  # type: ignore[arg-type]


def shape(changes: ChangeSet) -> list[tuple[str, str, str]]:
    """A ChangeSet without the keys of new rows (a dry run invents other public ids)."""
    return sorted((c.op, c.model, repr(sorted(c.fields))) for c in changes.changes)


def audit_rows() -> int:
    return AuditLog.objects.filter(action__startswith="catalog.").count()


# --- upsert


def test_a_new_sku_is_created_as_a_draft_and_audited(vendor: Vendor) -> None:
    report = services.upsert_products(vendor, [item("A-1"), item("A-2")], ctx=CTX)
    assert report.created == ["A-1", "A-2"]
    assert report.errors == []
    assert set(Product.objects.filter(vendor=vendor).values_list("status", flat=True)) == {"draft"}
    assert (
        AuditLog.objects.filter(
            action=services.PRODUCT_CREATED, target_model=services.PRODUCT_MODEL
        ).count()
        == 2
    )


def test_a_dry_run_writes_nothing_and_reports_the_real_changes(vendor: Vendor) -> None:
    existing = ProductFactory.create(vendor=vendor, sku="OLD", languages=[primary_language()])
    payload = [item("NEW"), item("OLD", price_net=Decimal("12.00"), weight_kg=Decimal("2.000"))]
    before = (Product.objects.count(), audit_rows())
    dry = services.upsert_products(vendor, payload, ctx=CTX, dry_run=True)
    assert (Product.objects.count(), audit_rows()) == before
    existing.refresh_from_db()
    assert existing.price_net == Decimal("10.00")
    real = services.upsert_products(vendor, payload, ctx=CTX)
    assert shape(dry.changes) == shape(real.changes)
    assert (dry.created, dry.updated, dry.errors) == (real.created, real.updated, real.errors)
    assert real.updated == ["OLD"]
    existing.refresh_from_db()
    assert existing.price_net == Decimal("12.00")
    assert existing.weight_kg == Decimal("2.000")
    assert AuditLog.objects.filter(action=services.PRODUCT_PRICE_CHANGED).count() == 1


def test_a_second_identical_upsert_changes_nothing(vendor: Vendor) -> None:
    payload = [item("A-1"), item("A-2", sales_mode="buy", country_of_origin="IT")]
    services.upsert_products(vendor, payload, ctx=CTX)
    rows = audit_rows()
    again = services.upsert_products(vendor, payload, ctx=CTX)
    assert again.changes.is_empty
    assert again.unchanged == ["A-1", "A-2"]
    assert again.created == again.updated == []
    assert audit_rows() == rows


def test_update_changes_texts_adds_a_language_and_keeps_others(vendor: Vendor) -> None:
    primary, other = primary_language(), other_language()
    services.upsert_products(
        vendor,
        [item("A-1", translations=(engine.TranslationIn(primary, "First title"),))],
        ctx=CTX,
    )
    product = Product.objects.get(vendor=vendor, sku="A-1")
    old_slug = product.translations.get(language=primary).slug
    report = services.upsert_products(
        vendor,
        [
            item(
                "A-1",
                translations=(
                    engine.TranslationIn(primary, "Second title", colour="Red"),
                    engine.TranslationIn(other, "Altro titolo"),
                ),
            )
        ],
        ctx=CTX,
    )
    assert report.updated == ["A-1"]
    texts = {t.language: t for t in product.translations.all()}
    assert texts[primary].title == "Second title"
    assert texts[primary].colour == "Red"
    assert texts[primary].slug != old_slug
    assert SlugHistory.objects.filter(product=product, slug=old_slug).exists()
    assert texts[other].title == "Altro titolo"
    assert services.upsert_products(
        vendor,
        [item("A-1", translations=(engine.TranslationIn(primary, "Second title", colour="Red"),))],
        ctx=CTX,
    ).unchanged == ["A-1"]
    assert ProductTranslation.objects.filter(product=product).count() == 2


def test_match_uses_the_sku_key_and_the_alias_history(vendor: Vendor) -> None:
    product = ProductFactory.create(vendor=vendor, sku="Ab 1", languages=[primary_language()])
    SkuAliasFactory.create(vendor=vendor, product=product, sku="OLD-9", sku_key="old-9")
    report = services.upsert_products(
        vendor,
        [item("ab  1", price_net=Decimal("11.00")), item("OLD-9", price_net=Decimal("11.00"))],
        ctx=CTX,
    )
    assert Product.objects.filter(vendor=vendor).count() == 1
    assert report.created == []
    assert [e.sku for e in report.errors] == ["OLD-9"]  # given twice: same product


def test_other_vendors_products_are_never_matched(vendor: Vendor) -> None:
    ProductFactory.create(sku="A-1")  # another vendor's
    report = services.upsert_products(vendor, [item("A-1")], ctx=CTX)
    assert report.created == ["A-1"]


def test_bad_rows_are_collected_and_the_rest_applies(vendor: Vendor) -> None:
    report = services.upsert_products(
        vendor,
        [item("OK-1"), item("BAD", price_net=Decimal("0.00")), item("OK-2"), item("OK-1")],
        ctx=CTX,
    )
    assert report.created == ["OK-1", "OK-2"]
    assert [(e.index, e.sku) for e in report.errors] == [(1, "BAD"), (3, "OK-1")]
    assert "price_net" in report.errors[0].errors
    assert not Product.objects.filter(sku="BAD").exists()
    assert Product.objects.filter(vendor=vendor).count() == 2


def test_an_ambiguous_match_stops_the_run_before_any_write(vendor: Vendor) -> None:
    a = ProductFactory.create(vendor=vendor, sku="X-1")
    ProductFactory.create(vendor=vendor, sku="X-2")
    SkuAliasFactory.create(vendor=vendor, product=a, sku="X-2", sku_key="x-2")
    with pytest.raises(services.AmbiguousSkuMatch):
        services.upsert_products(vendor, [item("NEW"), item("X-2")], ctx=CTX)
    assert not Product.objects.filter(sku="NEW").exists()


def test_existing_product_rules_are_refused_per_row(vendor: Vendor) -> None:
    ProductFactory.create(vendor=vendor, sku="S-1", languages=[primary_language()])
    VariantFactory.create(product=ProductFactory.create(vendor=vendor, kind="variable"), sku="V-1")
    clothing_attributes()
    report = services.upsert_products(
        vendor,
        [
            item("S-1", kind="variable"),
            item("S-1b"),
            item("V-1"),
            item("S-1", attributes=(engine.AttributeIn("author", text="x"),)),
        ],
        ctx=CTX,
    )
    errors = {e.index: e.errors for e in report.errors}
    assert "kind" in errors[0]
    assert "sku" in errors[2]
    assert 3 in errors
    assert report.created == ["S-1b"]


def test_a_variants_fields_are_updated(vendor: Vendor) -> None:
    clothing_attributes()
    primary = primary_language()

    def tee(stock: int, price: str) -> engine.ProductIn:
        return engine.ProductIn(
            sku="TEE",
            kind="variable",
            price_net=Decimal("19.90"),
            weight_kg=Decimal("0.2"),
            translations=(engine.TranslationIn(primary, "Tee"),),
            attributes=(engine.AttributeIn("size", values=("s", "m"), axis=True),),
            variants=(
                engine.VariantIn("TEE-S", {"size": "s"}, stock_qty=stock, is_default=True),
                engine.VariantIn("TEE-M", {"size": "m"}, price_net_override=Decimal(price)),
            ),
        )

    services.upsert_products(vendor, [tee(1, "21.00")], ctx=CTX)
    report = services.upsert_products(vendor, [tee(7, "22.00")], ctx=CTX)
    assert report.updated == ["TEE"]
    assert Variant.objects.get(sku="TEE-S").stock_qty == 7
    assert Variant.objects.get(sku="TEE-M").price_net_override == Decimal("22.00")
    assert services.upsert_products(vendor, [tee(7, "22.00")], ctx=CTX).unchanged == ["TEE"]


def test_an_unchanged_batch_reads_a_fixed_number_of_queries(vendor: Vendor) -> None:
    def run(count: int) -> int:
        payload = [item(f"Q{count}-{i}") for i in range(count)]
        services.upsert_products(vendor, payload, ctx=CTX)
        with CaptureQueriesContext(connection) as ctx:
            report = services.upsert_products(vendor, payload, ctx=CTX)
        assert report.changes.is_empty
        # the per-row savepoints are not reads
        return sum(1 for q in ctx if not q["sql"].startswith(("SAVEPOINT", "RELEASE")))

    assert run(2) == run(6)


# --- publish and retire


def with_text(**fields: object) -> Product:
    return ProductFactory.create(languages=[primary_language()], **fields)


def test_publish_goes_live_audits_and_skips_published() -> None:
    draft, live = with_text(), with_text(status="published")
    report = services.publish([draft, live], ctx=CTX)
    draft.refresh_from_db()
    assert draft.status == "published"
    assert len(report.changes.changes) == 1
    assert AuditLog.objects.filter(action=services.PRODUCT_STATUS_CHANGED).count() == 1
    assert services.publish([draft], ctx=CTX).changes.is_empty


def test_publish_dry_run_reports_the_same_changes_and_writes_nothing() -> None:
    product = with_text()
    dry = services.publish([product], ctx=CTX, dry_run=True)
    product.refresh_from_db()
    assert product.status == "draft"
    assert not AuditLog.objects.filter(action=services.PRODUCT_STATUS_CHANGED).exists()
    assert dry.changes.to_dict() == services.publish([product], ctx=CTX).changes.to_dict()


def test_publish_refuses_a_product_that_breaks_the_rules_and_says_why() -> None:
    no_text = ProductFactory.create()
    no_weight = with_text(weight_kg=None)
    fine = with_text()
    report = services.publish([no_text, no_weight, fine], ctx=CTX)
    assert set(report.refused) == {str(no_text.public_id), str(no_weight.public_id)}
    assert any("translation" in m for m in report.refused[str(no_text.public_id)]["product"])
    assert any("weight" in m for m in report.refused[str(no_weight.public_id)]["product"])
    assert Product.objects.get(pk=fine.pk).status == "published"
    assert Product.objects.get(pk=no_text.pk).status == "draft"
    # a catalogue-only product needs no weight
    assert (
        services.publish([with_text(sales_mode="catalog_only", weight_kg=None)], ctx=CTX).refused
        == {}
    )


def test_publish_runs_the_checks_other_apps_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(publishing._CHECKS, "gpsr", lambda product: ["GPSR data is missing."])
    product = with_text()
    report = services.publish([product], ctx=CTX)
    assert report.refused[str(product.public_id)] == {"gpsr": ["GPSR data is missing."]}
    assert Product.objects.get(pk=product.pk).status == "draft"


def test_retire_keeps_the_reason_and_leaves_stock() -> None:
    product = with_text(status="published", stock_qty=5)
    dry = services.retire([product], ctx=CTX, reason="discontinued", dry_run=True)
    assert Product.objects.get(pk=product.pk).status == "published"
    report = services.retire([product], ctx=CTX, reason="discontinued")
    assert report.changes.to_dict() == dry.changes.to_dict()
    stored = Product.objects.get(pk=product.pk)
    assert (stored.status, stored.stock_qty) == ("retired", 5)
    row = AuditLog.objects.get(action=services.PRODUCT_STATUS_CHANGED)
    assert row.after is not None
    assert row.after["reason"] == "discontinued"
    assert services.retire([product], ctx=CTX).changes.is_empty


def test_a_dry_run_publish_reads_a_fixed_number_of_queries() -> None:
    def run(count: int) -> int:
        products = [with_text() for _ in range(count)]
        with CaptureQueriesContext(connection) as ctx:
            services.publish(products, ctx=CTX, dry_run=True)
        return len(ctx)

    assert run(2) == run(6)

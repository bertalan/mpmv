"""MIG.3 part 1: the catalog loader, one test per rule (spec 13 §13.2, owner decisions)."""

import copy
from typing import Any

import pytest

from apps.catalog import publishing
from apps.catalog.models import (
    Attribute,
    Brand,
    Category,
    Product,
    ProductAttribute,
    ProductStatus,
    ProductTranslation,
    Taxonomy,
    Term,
    Variant,
    VariantOption,
    VariantTranslation,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, TranslationStatus
from apps.ingest import services
from apps.vendors.models import Vendor, VendorTranslation

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:migration", source="command")


@pytest.fixture(autouse=True)
def no_publication_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GPSR and restriction checks are CMP.3's; the loader only has to respect them."""
    monkeypatch.setattr(publishing, "_CHECKS", {})


def post(post_id: int, lang: str, group: int, sku: str, **extra: Any) -> dict[str, Any]:
    return {
        "post_id": post_id,
        "post_type": "product",
        "group_id": group,
        "lang": lang,
        "author_id": 7,
        "status": "publish",
        "sku": sku,
        "title": f"Bag {sku} {lang}",
        "price_net": "10.00",
        "stock_qty": "3",
        **extra,
    }


def bundle() -> dict[str, Any]:
    return {
        "vendors": [
            {
                "user_id": 7,
                "display_name": "Vendor A",
                "legal_name": "Vendor A Srl",
                "country": "IT",
                "email": "a@example.com",
                "commission_percent": "12.50",
                "vat_regime": "oss",
                "translations": {
                    "en": {"description": "<p>About</p>"},
                    "it": {"description": "<p>Chi siamo</p>"},
                },
            },
            {
                "user_id": 8,
                "display_name": "Vendor B",
                "legal_name": "Vendor B Srl",
                "country": "IT",
                "email": "b@example.com",
            },
        ],
        "categories": [
            {"term_id": 1, "group_id": 100, "lang": "en", "name": "Bags", "slug": "bags"},
            {"term_id": 2, "group_id": 100, "lang": "it", "name": "Borse", "slug": "borse"},
            {
                "term_id": 3,
                "group_id": 101,
                "lang": "en",
                "name": "Panniers",
                "slug": "panniers",
                "parent_id": 1,
            },
        ],
        "brands": [
            {"term_id": 20, "group_id": 200, "lang": "en", "name": "Maker", "slug": "maker"},
            {"term_id": 21, "group_id": 200, "lang": "it", "name": "Maker", "slug": "maker"},
        ],
        "tags": [
            {
                "term_id": 30,
                "group_id": 300,
                "lang": "en",
                "name": "Model X",
                "slug": "model-x",
                "role": "model",
            },
            {
                "term_id": 31,
                "group_id": 301,
                "lang": "en",
                "name": "Orphan",
                "slug": "orphan",
                "role": "leftover",
            },
            {"term_id": 32, "group_id": 302, "lang": "en", "name": "No role", "slug": "no-role"},
        ],
        "attributes": [
            {
                "code": "pa_color",
                "kind": "choice",
                "labels": {"en": "Colour", "it": "Colore"},
                "values": [
                    {"group_id": 1, "lang": "en", "value": "red", "label": "Red"},
                    {"group_id": 1, "lang": "it", "value": "red", "label": "Rosso"},
                ],
            },
            {"code": "colour", "kind": "choice", "values": []},
            {"code": "pa_width", "kind": "number", "unit": "cm"},
            {"code": "pa_unused", "kind": "text"},
        ],
        "posts": [
            post(
                10,
                "en",
                1,
                "A-1",
                category_ids=[3],
                brand_id=20,
                tag_ids=[30],
                attributes={"pa_width": "12.5", "pa_color": ["red"]},
                upsell_skus=["A-2"],
            ),
            post(11, "it", 1, "A-1"),
            post(12, "en", 2, "A-2", companion_sku="A-1"),  # no IT twin
        ],
    }


def load(data: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return services.load_legacy_catalog(data or bundle(), ctx=CTX, dry_run=False, **kwargs)


def test_dry_run_reports_the_changes_and_writes_nothing() -> None:
    report = services.load_legacy_catalog(bundle(), ctx=CTX)
    assert not report.changes.is_empty
    assert not Vendor.objects.exists()
    assert not Product.objects.exists()
    assert not Category.objects.exists()
    run = AuditLog.objects.get(action="ingest.catalog_load.run")
    assert (run.after or {})["dry_run"] is True


def test_a_real_run_loads_everything_and_audits_it() -> None:
    report = load()
    assert Vendor.objects.count() == 2
    assert Product.objects.count() == 2
    assert Category.objects.count() == 2
    assert Brand.objects.filter(name="Maker").exists()
    assert report.created["catalog.product"] == 2
    assert AuditLog.objects.filter(action="catalog.product.created").count() == 2
    assert AuditLog.objects.filter(action="vendor.created").count() == 2
    assert AuditLog.objects.filter(action="ingest.catalog_load.created").exists()
    run = AuditLog.objects.get(action="ingest.catalog_load.run")
    assert (run.after or {})["dry_run"] is False


def test_reload_creates_no_duplicates_and_changes_nothing() -> None:
    load()
    counts = {
        model: model.objects.count()
        for model in (
            Vendor,
            Product,
            Variant,
            Category,
            Brand,
            Term,
            Taxonomy,
            Attribute,
            ProductTranslation,
            VariantTranslation,
            ProductAttribute,
        )
    }
    before = AuditLog.objects.exclude(action="ingest.catalog_load.run").count()
    second = load()
    assert {model: model.objects.count() for model in counts} == counts
    assert second.created == {}
    assert second.skipped_existing > 0
    assert AuditLog.objects.exclude(action="ingest.catalog_load.run").count() == before


def test_a_vendor_loads_with_its_texts_as_published_translations_and_stays_applicant() -> None:
    report = load()
    vendor = Vendor.objects.get(legacy_wp_user_id=7)
    assert vendor.status == Vendor.Status.APPLICANT
    rows = {t.language: t for t in VendorTranslation.objects.filter(vendor=vendor)}
    assert rows["it"].description == "<p>Chi siamo</p>"
    assert rows["it"].status == TranslationStatus.PUBLISHED
    assert rows["en"].status == TranslationStatus.PUBLISHED
    # Hooks: commission (CommissionRule, VEN.2) and the VAT regime (tax app) are listed, not loaded.
    assert report.deferred["commission"] == [f"vendor {vendor.public_id}: 12.50"]
    assert report.deferred["vat_regime"] == [f"vendor {vendor.public_id}: oss"]


def test_products_are_keyed_by_vendor_sku_and_group_never_by_sku_alone() -> None:
    data = bundle()
    data["posts"] = [
        post(10, "en", 1, "SAME"),
        post(11, "it", 1, "SAME"),
        {**post(20, "en", 2, "same"), "author_id": 8},  # other vendor: same SKU is fine
        post(30, "en", 3, "SAME "),  # same vendor and SKU key, another group: not merged
    ]
    report = load(data)
    assert Product.objects.filter(sku_key="same").count() == 2
    assert {p.vendor.legacy_wp_user_id for p in Product.objects.all()} == {7, 8}
    first = Product.objects.get(vendor__legacy_wp_user_id=7)
    assert (first.legacy_wp_id_en, first.legacy_wp_id_it) == (10, 11)
    assert any("also used by translation group" in problem for problem in report.problems)


def test_a_twin_pair_becomes_one_product_with_two_published_texts() -> None:
    load()
    product = Product.objects.get(sku="A-1")
    texts = {t.language: t for t in product.translations.all()}
    assert set(texts) == {"en", "it"}  # the legacy languages only: no placeholder for de, fr, es
    assert texts["it"].status == TranslationStatus.PUBLISHED
    assert texts["it"].slug
    assert texts["en"].slug
    assert not product.translation("it").is_fallback


def test_a_product_with_no_it_twin_is_en_only_with_fallback_and_a_queue_entry() -> None:
    report = load()
    product = Product.objects.get(sku="A-2")
    it = product.translations.get(language="it")
    assert it.status == TranslationStatus.MACHINE  # the translation-queue entry (I18N.4)
    assert product.translation("it").is_fallback  # the IT page shows the fallback notice
    english = product.translation("en").translation
    assert english is not None
    assert english.title == "Bag A-2 en"
    assert "product 'A-2' [it]" in report.translation_queue


def test_variations_load_with_options_and_an_untranslated_one_goes_to_the_queue() -> None:
    data = bundle()
    data["posts"] = [
        post(10, "en", 1, "V-1", type="variable", price_net=""),
        post(11, "it", 1, "V-1", type="variable", price_net=""),
        {
            **post(100, "en", 50, "V-1-R", price_net="15.00", options={"pa_color": "red"}),
            "post_type": "product_variation",
            "parent_id": 10,
        },
        {
            **post(101, "it", 50, "V-1-R", price_net="15.00", options={"pa_color": "red"}),
            "post_type": "product_variation",
            "parent_id": 11,
        },
        {
            **post(102, "en", 51, "V-1-B", price_net="18.00", options={"pa_color": "blue"}),
            "post_type": "product_variation",
            "parent_id": 10,
        },  # no IT twin
    ]
    report = load(data)
    product = Product.objects.get(sku="V-1")
    assert product.kind == "variable"
    assert str(product.price_net) == "15.00"  # the lowest variation price
    assert product.variants.count() == 2
    assert VariantOption.objects.count() == 2
    assert ProductAttribute.objects.get(product=product, attribute__code="color").is_variant_axis
    queued = Variant.objects.get(sku="V-1-B").translations.get(language="it")
    assert queued.status == TranslationStatus.MACHINE
    assert "variant 'V-1-B' [it]" in report.translation_queue
    assert Variant.objects.get(sku="V-1-R").translations.get(language="it").status == "published"


def test_drafts_and_archived_load_hidden_only_when_the_vendor_chose_so() -> None:
    data = bundle()
    data["posts"] = [
        post(10, "en", 1, "P-1"),
        post(11, "en", 2, "D-1", status="draft"),
        post(12, "en", 3, "R-1", status="archived"),
    ]
    report = load(data)
    assert set(Product.objects.values_list("sku", flat=True)) == {"P-1"}
    assert len(report.skipped_unpublished) == 2
    assert Product.objects.get(sku="P-1").status == ProductStatus.PUBLISHED

    chosen = load(data, include_unpublished=True)
    assert set(Product.objects.values_list("sku", flat=True)) == {"P-1", "D-1", "R-1"}
    assert Product.objects.get(sku="D-1").status == ProductStatus.DRAFT
    assert Product.objects.get(sku="R-1").status == ProductStatus.DRAFT
    assert chosen.created["catalog.product"] == 2


def test_trashed_products_are_only_redirect_sources() -> None:
    data = bundle()
    data["posts"] = [post(10, "en", 1, "T-1", status="trash"), post(11, "en", 2, "L-1")]
    report = load(data, include_unpublished=True)
    assert set(Product.objects.values_list("sku", flat=True)) == {"L-1"}
    assert report.redirect_sources == ["post 10"]


def test_a_publication_check_that_blocks_keeps_a_live_product_hidden_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {"gpsr": lambda product: ["GPSR data missing"]})
    data = bundle()
    data["posts"] = [post(10, "en", 1, "G-1")]
    report = load(data)
    assert Product.objects.get(sku="G-1").status == ProductStatus.DRAFT
    assert report.held_back == ["product 'G-1': gpsr"]


def test_commission_override_of_a_product_is_listed_for_the_commission_rule_follow_up() -> None:
    data = bundle()
    data["posts"] = [post(10, "en", 1, "C-1", commission_override_percent="8.00")]
    report = load(data)
    product = Product.objects.get(sku="C-1")
    assert report.deferred["commission"][-1] == f"product {product.public_id}: 8.00"


def test_taxonomies_brands_and_typed_attributes_follow_the_data_map() -> None:
    report = load()
    product = Product.objects.get(sku="A-1")
    assert product.category is not None
    assert product.category.get_parent() == Category.objects.get(translations__slug="bags")
    assert product.brand == Brand.objects.get(name="Maker")
    assert [t.taxonomy.code for t in product.terms.all()] == ["model"]
    attributes = {pa.attribute.code: pa for pa in product.attributes.select_related("attribute")}
    assert str(attributes["width"].numeric_value) == "12.500"
    assert [v.value for v in attributes["color"].values.all()] == ["red"]
    it_label = attributes["color"].values.get().translations.get(language="it").label
    assert it_label == "Rosso"
    # The tag with no role waits for MIG.2; the unused taxonomy and attribute are dropped.
    assert not Taxonomy.objects.filter(code="leftover").exists()
    assert not Attribute.objects.filter(code="unused").exists()
    assert any("leftover" in item for item in report.dropped)
    assert any("unused" in item for item in report.dropped)
    assert any("no role" in problem for problem in report.problems)


def test_attribute_aliases_merge_typos_and_duplicates() -> None:
    data = bundle()
    data["posts"] = [post(10, "en", 1, "AL-1", attributes={"colour": ["red"]})]
    load(data, attribute_aliases={"colour": "pa_color"})
    assert Attribute.objects.filter(code="color").count() == 1
    assert not Attribute.objects.filter(code="colour").exists()


def test_companions_and_upsells_link_by_sku_within_the_vendor() -> None:
    load()
    a2 = Product.objects.get(sku="A-2")
    a1 = Product.objects.get(sku="A-1")
    assert a2.required_companion == a1
    assert list(a1.upsells.all()) == [a2]


def test_a_malformed_price_rejects_only_that_product() -> None:
    data = bundle()
    data["posts"] = [post(10, "en", 1, "BAD-1", price_net="1,50 EUR"), post(11, "en", 2, "OK-1")]
    report = load(data)
    assert set(Product.objects.values_list("sku", flat=True)) == {"OK-1"}
    assert any("bad-1" in problem and "not a number" in problem for problem in report.problems)


def test_a_product_of_an_unknown_vendor_is_rejected() -> None:
    data = bundle()
    data["posts"] = [{**post(10, "en", 1, "NV-1"), "author_id": 999}]
    report = load(data)
    assert not Product.objects.exists()
    assert any("vendor 999 is not loaded" in problem for problem in report.problems)


def test_load_never_mutates_the_input() -> None:
    data = bundle()
    snapshot = copy.deepcopy(data)
    load(data)
    assert data == snapshot


def test_the_dry_run_changeset_matches_the_real_run() -> None:
    dry = services.load_legacy_catalog(bundle(), ctx=CTX)
    real = load()
    assert dry.changes.summary() == real.changes.summary()
    assert dry.created == real.created


def test_invalid_input_is_a_report_not_a_crash() -> None:
    data = bundle()
    data["categories"].append(
        {"term_id": 9, "group_id": 109, "lang": "en", "name": "Orphan", "parent_id": 999}
    )
    report = load(data)
    assert any("unknown parent 999" in problem for problem in report.problems)

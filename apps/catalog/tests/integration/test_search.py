"""CAT.9: product search (spec 03 §3.1 and §3.2): PostgreSQL full-text search in the language's
configuration, trigram similarity for typos, and a SKU prefix (four characters or more) on the
SKU match key of products and variants, a variant leading to its product.

Only listed products are found (published, of an active vendor), by their published text in the
requested language. A vendor filter narrows the search to one tenant. Every query is served by an
index (GIN on the text vector, GIN trigram on the title, a pattern index on the match key): never
an unbounded LIKE scan.
"""

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog import services
from apps.catalog.models import Product, ProductTranslation
from apps.catalog.services import SearchFilter
from apps.catalog.tests.factories import (
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = ProductTranslation.Status.PUBLISHED


def active_vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE)


def listed(title: str = "Plain item", language: str = "en", **kwargs: Any) -> Product:
    """A published product of an active vendor with one published text."""
    kwargs.setdefault("vendor", active_vendor())
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(**kwargs)
    ProductTranslationFactory.create(
        product=product,
        language=language,
        title=title,
        short_description="",
        description="",
        status=PUBLISHED,
    )
    return product


def found(query: str, language: str = "en", **kwargs: Any) -> list[int]:
    return [p.pk for p in services.search(query, language, **kwargs)]


# --- SKU match key and prefix (spec 03 §3.1 "Search by SKU", §3.2 identity)


def test_bag_100_finds_bag_double_space_100() -> None:
    product = listed(sku="BAG  100")
    assert found("bag 100") == [product.pk]


def test_a_partial_sku_of_four_characters_or_more_finds_the_products_it_starts() -> None:
    first = listed(sku="BAG 100")
    second = listed(sku="BAG 150")
    listed(sku="BOX 100")
    assert set(found("bag 1")) == {first.pk, second.pk}
    assert set(found("BAG ")) == set()  # trimmed to three characters: no prefix search


def test_a_sku_prefix_shorter_than_four_characters_finds_nothing_by_sku() -> None:
    listed(sku="ZQX-9001")
    assert found("zqx") == []


def test_a_short_query_still_finds_a_whole_sku() -> None:
    product = listed(sku="Q7")
    assert found("q7") == [product.pk]


def test_a_variant_sku_finds_its_product() -> None:
    parent = listed(sku="SADDLE", kind=Product.Kind.VARIABLE)
    VariantFactory.create(product=parent, sku="SADDLE / Red XL")
    VariantFactory.create(product=parent, sku="SADDLE / Red L")
    assert found("saddle / red") == [parent.pk]  # once, although two variants match


def test_like_wildcards_in_the_query_match_themselves_only() -> None:
    listed(sku="ABCD-1")
    listed(sku="WXYZ-2")
    assert found("____") == []
    assert found("%%%%") == []
    percent = listed(sku="50% OFF-1")
    assert found("50% o") == [percent.pk]


def test_an_exact_sku_ranks_before_a_longer_sku_and_before_text() -> None:
    longer = listed(sku="LAMP-100-B")
    exact = listed(sku="lamp-100")
    texted = listed(title="lamp-100 spare bulb")
    assert found("LAMP-100") == [exact.pk, longer.pk, texted.pk]


# --- Full-text search per language, trigram for typos


def test_full_text_search_uses_the_language_stemming() -> None:
    english = listed(title="Leather saddle bags", language="en")
    italian = listed(title="Borsa da sella in pelle", language="it")
    assert found("bag", "en") == [english.pk]
    assert found("borse", "it") == [italian.pk]


def test_words_may_come_in_any_order() -> None:
    product = listed(title="Waterproof tank bag")
    assert found("bag waterproof") == [product.pk]


def test_a_typo_still_finds_the_product() -> None:
    product = listed(title="Leather saddlebag")
    assert found("saddlebga") == [product.pk]
    assert found("lether") == [product.pk]


def test_the_short_description_and_description_are_searched() -> None:
    product = listed(title="Tank bag")
    product.translations.update(short_description="Magnetic mount", description="<p>Velcro</p>")
    assert found("magnetic") == [product.pk]
    assert found("velcro") == [product.pk]


def test_a_title_match_ranks_before_a_description_match() -> None:
    described = listed(title="Side case")
    described.translations.update(description="Fits a pannier rack")
    titled = listed(title="Pannier liner")
    assert found("pannier") == [titled.pk, described.pk]


def test_only_texts_published_in_the_requested_language_are_searched() -> None:
    product = listed(title="Borsa serbatoio", language="it")
    ProductTranslationFactory.create(
        product=product, language="en", title="Tank bag", status=ProductTranslation.Status.MACHINE
    )
    # Saving the EN source sent the IT text back to review (I18N.2); publish it again.
    product.translations.filter(language="it").update(status=PUBLISHED)
    assert found("serbatoio", "it") == [product.pk]
    assert found("serbatoio", "en") == []
    assert found("tank", "en") == []  # a machine draft is not public
    assert found("tank", "it") == []


def test_a_sku_is_language_neutral() -> None:
    product = listed(sku="TB-2040", title="Borsa serbatoio", language="it")
    assert found("tb-2040", "en") == [product.pk]
    assert found("tb-2040", "de") == [product.pk]


# --- Visibility and tenancy


@pytest.mark.parametrize(
    ("product_status", "vendor_status"),
    [
        (Product.Status.DRAFT, Vendor.Status.ACTIVE),
        (Product.Status.RETIRED, Vendor.Status.ACTIVE),
        (Product.Status.PUBLISHED, Vendor.Status.ONBOARDING),
        (Product.Status.PUBLISHED, Vendor.Status.SUSPENDED),
        (Product.Status.PUBLISHED, Vendor.Status.CLOSED),
    ],
)
def test_products_that_are_not_listed_are_never_found(
    product_status: str, vendor_status: str
) -> None:
    hidden = listed(
        sku="HIDE-0001",
        title="Hidden heated gloves",
        status=product_status,
        vendor=VendorFactory.create(status=vendor_status),
        kind=Product.Kind.VARIABLE,
    )
    VariantFactory.create(product=hidden, sku="HIDE-VAR-1")
    assert found("hide-0001") == []
    assert found("heated gloves") == []
    assert found("hide-var") == []


def test_a_vendor_filter_keeps_the_search_inside_that_vendor() -> None:
    mine = listed(sku="SHARED-1", title="Rain cover")
    theirs = listed(sku="SHARED-1", title="Rain cover")
    assert set(found("shared-1")) == {mine.pk, theirs.pk}  # the same SKU at two vendors
    assert found("shared-1", filters=SearchFilter(vendor=mine.vendor)) == [mine.pk]
    assert found("rain cover", filters=SearchFilter(vendor=theirs.vendor)) == [theirs.pk]


def test_a_too_short_query_finds_nothing_and_runs_no_query() -> None:
    listed(title="A")
    with CaptureQueriesContext(connection) as queries:
        assert found("a") == []
        assert found("   ") == []
    assert len(queries) == 0


def test_the_result_is_a_queryset_of_products() -> None:
    product = listed(sku="CASE-0001")
    results = services.search("case-0001", "en")
    assert results.model is Product
    assert results.select_related("vendor").get().pk == product.pk


def test_the_number_of_results_is_bounded() -> None:
    for n in range(5):
        listed(sku=f"BULK-{n:04d}")
    assert len(found("bulk-", limit=3)) == 3


# --- Indexes: no unbounded scans


def test_the_trigram_extension_is_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        assert cursor.fetchone() is not None


@pytest.mark.parametrize("language", ["en", "it", "de", "fr", "es"])
def test_every_search_query_is_served_by_an_index(language: str) -> None:
    """With a few rows the planner would rather walk any small index and filter. Here plain
    sequential and index scans are disabled, so a table is read through an index condition
    (a bitmap scan) or by a sequential scan that shows in the plan: each text, trigram and SKU
    query must name its index."""
    with connection.cursor() as cursor:  # rolled back with the test transaction
        # Its (product, language) index would answer `language = ...` without the text index.
        cursor.execute(
            "ALTER TABLE catalog_producttranslation"
            " DROP CONSTRAINT catalog_producttranslation_unique"
        )
    listed(sku="IDX-0001", title="Index probe", language=language)
    with CaptureQueriesContext(connection) as queries:
        list(services.search("idx-0001 probe", language))
    plans = []
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_indexscan = off")
        cursor.execute("SET LOCAL enable_indexonlyscan = off")
        cursor.execute("SET LOCAL enable_nestloop = off")
        for query in queries.captured_queries:
            sql = query["sql"]
            if not sql.lstrip().upper().startswith("SELECT"):
                continue
            cursor.execute(f"EXPLAIN {sql}")
            plans.append("\n".join(row[0] for row in cursor.fetchall()))
    joined = "\n".join(plans)
    assert "Seq Scan on catalog_producttranslation" not in joined
    assert "Seq Scan on catalog_variant" not in joined
    assert f"catalog_ptrans_fts_{language}" in joined
    assert "catalog_ptrans_title_trgm" in joined
    assert "catalog_product_sku_key_like" in joined
    assert "catalog_variant_sku_key_like" in joined

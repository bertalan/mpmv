"""CAT.8 (part 1/3): the listing query (spec 03 §3.2, owner decisions D-07 and Q-31).

A listing shows the published products of active vendors, inside a fixed scope (a category, a
taxonomy term with its children, a brand or a vendor storefront) and narrowed by the visitor's
filters. Filter values are slugs in the page language with the explicit EN fallback (D-18).
Products without a public price (on request, catalog only) never match a price filter. The same
query feeds the CMS product grid block (CMS.10).
"""

from decimal import Decimal
from typing import Any

import pytest

from apps.catalog import services
from apps.catalog.models import Product
from apps.catalog.services import ListingFilter, ListingScope
from apps.catalog.tests.factories import (
    BrandFactory,
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def active_vendor(**kwargs: Any) -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE, sells_online=True, **kwargs)


def listed(**kwargs: Any) -> Product:
    """A published product of an active vendor (the vendor of `vendor=` if given)."""
    kwargs.setdefault("vendor", active_vendor())
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    return ProductFactory.create(**kwargs)


def pks(products: Any) -> set[int]:
    return {p.pk for p in products}


# --- Visibility


def test_only_published_products_of_active_vendors_are_listed() -> None:
    shown = listed()
    listed(status=Product.Status.DRAFT)
    listed(status=Product.Status.RETIRED)
    listed(vendor=VendorFactory.create(status=Vendor.Status.ONBOARDING))
    listed(vendor=VendorFactory.create(status=Vendor.Status.SUSPENDED))
    listed(vendor=VendorFactory.create(status=Vendor.Status.CLOSED))
    assert pks(services.listed_products()) == {shown.pk}
    assert pks(services.listing_products()) == {shown.pk}


def test_products_without_a_public_price_are_listed_without_a_price_filter() -> None:
    buy = listed()
    on_request = listed(sales_mode=Product.SalesMode.ON_REQUEST, price_net=Decimal("5.00"))
    catalog_only = listed(sales_mode=Product.SalesMode.CATALOG_ONLY, price_net=None)
    assert pks(services.listing_products()) == {buy.pk, on_request.pk, catalog_only.pk}


def test_a_price_filter_never_matches_a_product_without_a_public_price() -> None:
    buy = listed(price_net=Decimal("5.00"))
    listed(sales_mode=Product.SalesMode.ON_REQUEST, price_net=Decimal("5.00"))
    listed(sales_mode=Product.SalesMode.CATALOG_ONLY, price_net=None)
    only_max = ListingFilter(price_max=Decimal("100.00"))
    assert pks(services.listing_products(filters=only_max)) == {buy.pk}
    only_min = ListingFilter(price_min=Decimal("0.00"))
    assert pks(services.listing_products(filters=only_min)) == {buy.pk}


def test_the_price_range_includes_both_bounds() -> None:
    ten = listed(price_net=Decimal("10.00"))
    twenty = listed(price_net=Decimal("20.00"))
    listed(price_net=Decimal("20.01"))
    listed(price_net=Decimal("9.99"))
    window = ListingFilter(price_min=Decimal("10.00"), price_max=Decimal("20.00"))
    assert pks(services.listing_products(filters=window)) == {ten.pk, twenty.pk}


# --- Scopes


def test_a_term_scope_includes_the_products_of_its_child_terms() -> None:
    maker = TermFactory.create()
    model = TermFactory.create(parent=maker)
    other = TermFactory.create(taxonomy=maker.taxonomy)
    on_maker, on_model, on_other = listed(), listed(), listed()
    on_maker.terms.add(maker)
    on_model.terms.add(model)
    on_other.terms.add(other)
    maker.refresh_from_db()
    assert pks(services.listing_products(ListingScope(term=maker))) == {on_maker.pk, on_model.pk}
    assert pks(services.listing_products(ListingScope(term=model))) == {on_model.pk}


def test_a_category_scope_covers_its_subtree_and_secondary_categories() -> None:
    bags = CategoryFactory.create()
    backpacks = CategoryFactory.create(parent=bags)
    hats = CategoryFactory.create()
    in_bags = listed(category=bags)
    in_backpacks = listed(category=backpacks)
    secondary = listed(category=hats)
    secondary.secondary_categories.add(backpacks)
    listed(category=hats)
    bags.refresh_from_db()
    assert pks(services.listing_products(ListingScope(category=bags))) == {
        in_bags.pk,
        in_backpacks.pk,
        secondary.pk,
    }


def test_brand_and_vendor_storefront_scopes() -> None:
    brand = BrandFactory.create()
    vendor = active_vendor()
    branded = listed(brand=brand)
    own = listed(vendor=vendor)
    listed()
    assert pks(services.listing_products(ListingScope(brand=brand))) == {branded.pk}
    assert pks(services.listing_products(ListingScope(vendor=vendor))) == {own.pk}


def test_a_product_matching_several_ways_is_listed_once() -> None:
    bags = CategoryFactory.create()
    backpacks = CategoryFactory.create(parent=bags)
    maker = TermFactory.create()
    model = TermFactory.create(parent=maker)
    product = listed(category=bags)
    product.secondary_categories.add(backpacks)
    product.terms.add(maker, model)
    bags.refresh_from_db()
    maker.refresh_from_db()
    scope = ListingScope(category=bags, term=maker)
    assert list(services.listing_products(scope)) == [product]


# --- Filters resolved from slugs


def taxonomy_with_terms(slug_en: str, slug_it: str | None = None) -> Any:
    taxonomy = TaxonomyFactory.create()
    TaxonomyTranslationFactory.create(taxonomy=taxonomy, language="en", slug=slug_en)
    if slug_it:
        TaxonomyTranslationFactory.create(taxonomy=taxonomy, language="it", slug=slug_it)
    return taxonomy


def term(taxonomy: Any, slug_en: str, slug_it: str | None = None, parent: Any = None) -> Any:
    node = TermFactory.create(taxonomy=taxonomy, parent=parent)
    TermTranslationFactory.create(term=node, language="en", slug=slug_en)
    if slug_it:
        TermTranslationFactory.create(term=node, language="it", slug=slug_it)
    return node


def test_term_keys_are_the_slugs_of_the_filterable_taxonomies_in_the_page_language() -> None:
    occasion = taxonomy_with_terms("occasion", "occasione")
    colour = taxonomy_with_terms("colour")  # no IT slug: EN fallback
    hidden = taxonomy_with_terms("internal")
    hidden.filterable = False
    hidden.save()
    assert services.listing_term_keys("en") == {"occasion": occasion, "colour": colour}
    assert services.listing_term_keys("it") == {"occasione": occasion, "colour": colour}


def test_term_filters_or_within_a_taxonomy_and_across_taxonomies() -> None:
    occasion = taxonomy_with_terms("occasion")
    colour = taxonomy_with_terms("colour")
    party, wedding = term(occasion, "party"), term(occasion, "wedding")
    red = term(colour, "red")
    party_red, wedding_red, party_only = listed(), listed(), listed()
    party_red.terms.add(party, red)
    wedding_red.terms.add(wedding, red)
    party_only.terms.add(party)

    def matching(query: dict[str, list[str]]) -> set[int]:
        params = services.parse_listing_query(query, "en").params
        resolved = services.resolve_listing_filter(params, "en")
        assert resolved.unknown == ()
        return pks(services.listing_products(filters=resolved.filters))

    assert matching({"occasion": ["party,wedding"]}) == {
        party_red.pk,
        wedding_red.pk,
        party_only.pk,
    }
    assert matching({"occasion": ["party"], "colour": ["red"]}) == {party_red.pk}


def test_a_term_filter_includes_child_terms() -> None:
    compatibility = taxonomy_with_terms("compatibility")
    maker = term(compatibility, "maker-1")
    model = term(compatibility, "model-1", parent=maker)
    product = listed()
    product.terms.add(model)
    params = services.parse_listing_query({"compatibility": ["maker-1"]}, "en").params
    resolved = services.resolve_listing_filter(params, "en")
    assert pks(services.listing_products(filters=resolved.filters)) == {product.pk}


def test_filter_slugs_resolve_in_the_page_language_with_the_english_fallback() -> None:
    occasion = taxonomy_with_terms("occasion", "occasione")
    party = term(occasion, "party", "festa")
    picnic = term(occasion, "picnic")  # no IT slug
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, language="en", slug="bags")
    CategoryTranslationFactory.create(category=bags, language="it", slug="borse")
    on_party, on_picnic = listed(category=bags), listed(category=bags)
    on_party.terms.add(party)
    on_picnic.terms.add(picnic)

    query = {"occasione": ["festa,picnic"], "category": ["borse"]}
    params = services.parse_listing_query(query, "it").params
    resolved = services.resolve_listing_filter(params, "it")
    assert resolved.unknown == ()
    assert pks(services.listing_products(filters=resolved.filters)) == {on_party.pk, on_picnic.pk}


def resolve(query: dict[str, list[str]], language: str = "en") -> services.ResolvedFilter:
    params = services.parse_listing_query(query, language).params
    return services.resolve_listing_filter(params, language)


def category(slug_en: str, parent: Any = None, slug_it: str | None = None) -> Any:
    node = CategoryFactory.create(parent=parent)
    CategoryTranslationFactory.create(category=node, language="en", slug=slug_en)
    if slug_it:
        CategoryTranslationFactory.create(category=node, language="it", slug=slug_it)
    return node


def test_a_nested_category_value_is_its_slug_path_so_equal_slugs_never_widen_it() -> None:
    # Slugs are unique among siblings only: /men/bags and /women/bags share "bags".
    men, women = category("men"), category("women")
    men_bags, women_bags = category("bags", parent=men), category("bags", parent=women)
    in_men, in_women = listed(category=men_bags), listed(category=women_bags)

    resolved = resolve({"category": ["men/bags"]})
    assert resolved.unknown == ()
    assert pks(services.listing_products(filters=resolved.filters)) == {in_men.pk}
    women_only = resolve({"category": ["women/bags"]})
    assert pks(services.listing_products(filters=women_only.filters)) == {in_women.pk}

    not_top_level = resolve({"category": ["bags"]})
    assert not_top_level.unknown == ("category",)
    assert not services.listing_products(filters=not_top_level.filters).exists()


def test_a_nested_term_value_is_its_slug_path_so_equal_slugs_never_widen_it() -> None:
    compatibility = taxonomy_with_terms("compatibility")
    maker_1, maker_2 = term(compatibility, "maker-1"), term(compatibility, "maker-2")
    model_1 = term(compatibility, "model-x", parent=maker_1)
    model_2 = term(compatibility, "model-x", parent=maker_2)
    on_1, on_2 = listed(), listed()
    on_1.terms.add(model_1)
    on_2.terms.add(model_2)

    resolved = resolve({"compatibility": ["maker-1/model-x"]})
    assert resolved.unknown == ()
    assert pks(services.listing_products(filters=resolved.filters)) == {on_1.pk}

    both = resolve({"compatibility": ["maker-1/model-x,maker-2/model-x"]})
    assert both.unknown == ()
    assert pks(services.listing_products(filters=both.filters)) == {on_1.pk, on_2.pk}

    assert resolve({"compatibility": ["model-x"]}).unknown == ("compatibility",)
    assert resolve({"compatibility": ["maker-2/model-y"]}).unknown == ("compatibility",)


def test_a_term_path_never_crosses_into_another_taxonomy() -> None:
    compatibility = taxonomy_with_terms("compatibility")
    other = taxonomy_with_terms("other")
    term(other, "maker-1")
    maker = term(compatibility, "maker-1")
    term(compatibility, "model-x", parent=maker)
    assert resolve({"other": ["maker-1/model-x"]}).unknown == ("other",)
    assert resolve({"compatibility": ["maker-1/model-x"]}).unknown == ()


def test_a_slug_path_resolves_each_level_in_the_page_language_with_the_english_fallback() -> None:
    men = category("men", slug_it="uomo")
    bags = category("bags", parent=men)  # no IT slug: EN fallback
    product = listed(category=bags)
    resolved = resolve({"category": ["uomo/bags"]}, "it")
    assert resolved.unknown == ()
    assert pks(services.listing_products(filters=resolved.filters)) == {product.pk}
    # On the IT page a level is matched by its IT slug, never by the EN slug it overrides.
    assert resolve({"category": ["men/bags"]}, "it").unknown == ("category",)


def test_a_slug_two_siblings_answer_to_through_the_fallback_is_ambiguous() -> None:
    # Sibling uniqueness is per language: one sibling's IT slug can equal another's EN slug.
    shoes = category("shoes", slug_it="borse")
    bags = category("borse")  # EN only: "borse" on the IT page too
    listed(category=shoes)
    listed(category=bags)
    assert resolve({"category": ["borse"]}, "it").unknown == ("category",)
    assert resolve({"category": ["borse"]}, "en").unknown == ()


def test_a_taxonomy_key_prefers_the_page_language_over_another_taxonomys_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The slug constraint is per language: X's IT slug can equal Y's EN slug when Y has no IT.
    x = taxonomy_with_terms("colour", "colore")
    y = taxonomy_with_terms("colore")
    red = term(x, "red", "rosso")
    product = listed()
    product.terms.add(red)
    with caplog.at_level("WARNING", logger="apps.catalog.services"):
        keys = services.listing_term_keys("it")
    assert keys["colore"] == x
    assert y not in keys.values()
    assert "colore" in caplog.text
    resolved = resolve({"colore": ["rosso"]}, "it")
    assert resolved.unknown == ()
    assert pks(services.listing_products(filters=resolved.filters)) == {product.pk}
    assert services.listing_term_keys("en") == {"colour": x, "colore": y}


def test_the_vendor_filter_takes_an_active_vendor_slug() -> None:
    vendor = active_vendor(slug="vendor-one")
    own = listed(vendor=vendor)
    listed()
    params = services.parse_listing_query({"vendor": ["vendor-one"]}, "en").params
    resolved = services.resolve_listing_filter(params, "en")
    assert resolved.filters.vendor == vendor
    assert pks(services.listing_products(filters=resolved.filters)) == {own.pk}


def test_an_unknown_slug_is_reported_and_matches_nothing() -> None:
    occasion = taxonomy_with_terms("occasion")
    party = term(occasion, "party")
    product = listed()
    product.terms.add(party)
    VendorFactory.create(slug="closed-vendor", status=Vendor.Status.CLOSED)
    query = {"occasion": ["party,nothing"], "category": ["nowhere"], "vendor": ["closed-vendor"]}
    params = services.parse_listing_query(query, "en").params
    resolved = services.resolve_listing_filter(params, "en")
    assert resolved.unknown == ("category", "occasion", "vendor")
    assert not services.listing_products(filters=resolved.filters).exists()


# --- Pages (shared by the listing views and the CMS product grid)


def test_a_listing_page_is_ordered_newest_first_and_paginated() -> None:
    products = [listed() for _ in range(5)]
    newest_first = [p.pk for p in reversed(products)]
    params = services.parse_listing_query({"page": ["2"]}, "en").params
    page = services.listing_page(ListingScope(), params, "en", per_page=2)
    assert [p.pk for p in page.products] == newest_first[2:4]
    assert page.window.total == 5
    assert page.window.page_count == 3
    assert page.unknown == ()
    assert not page.window.out_of_range


def test_a_listing_page_past_the_end_is_out_of_range_and_empty() -> None:
    listed()
    params = services.parse_listing_query({"page": ["9"]}, "en").params
    page = services.listing_page(ListingScope(), params, "en", per_page=2)
    assert page.window.out_of_range
    assert page.products == []


def test_a_product_grid_reuses_the_listing_query_with_a_limit() -> None:
    occasion = taxonomy_with_terms("occasion")
    party = term(occasion, "party")
    tagged = [listed() for _ in range(3)]
    for product in tagged:
        product.terms.add(party)
    listed()
    party.refresh_from_db()
    grid = services.listing_products(ListingScope(term=party))[:2]
    assert [p.pk for p in grid] == [tagged[2].pk, tagged[1].pk]

"""CAT.27: the public search results page (owner decision 2026-11-17).

`/?s=<query>` (and `/<lang>/?s=`) answers on the language home with a results page: products
first (CAT.9 search with the CAT.8 listing filters and numbered pagination), then short groups of
events, articles and vendors. A plain GET form, `noindex, follow`, never cached per query,
length-limited and rate-limited.
"""

import re
import secrets
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from wagtail.models import Site

from apps.catalog import search_page, services
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    VariantFactory,
)
from apps.cms.services import seed_site
from apps.cms.tests.factories import BlogPostPageFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.events.tests.factories import EventPageFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.i18n import language_prefix, other_language

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
NOINDEX_FOLLOW = '<meta name="robots" content="noindex, follow">'


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def active_vendor(**kwargs: Any) -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE, **kwargs)


def listed(title: str, language: str | None = None, **kwargs: Any) -> Product:
    kwargs.setdefault("vendor", active_vendor())
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product = ProductFactory.create(**kwargs)
    ProductTranslationFactory.create(
        product=product,
        language=language or primary_language(),
        title=title,
        slug=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
        short_description="",
        description="",
        status=PUBLISHED,
    )
    return product


def search(client: Client, query: str, language: str | None = None, **extra: str) -> Any:
    prefix = language_prefix(language) if language else ""
    return client.get(f"{prefix}/", {"s": query, **extra})


def html_of(response: Any) -> str:
    assert response.status_code == 200, response.status_code
    return str(response.content.decode())


def public_event(title: str, **kwargs: Any) -> Any:
    return EventPageFactory.create(title=title, live=True, **kwargs)


# --- Done when: the URL, the groups and their order


def test_products_come_first_and_the_other_groups_below(client: Client) -> None:
    listed("Carburetor kit")
    public_event("Carburetor fair")
    BlogPostPageFactory.create(title="Carburetor tuning guide")
    active_vendor(display_name="Carburetor Works", slug="carburetor-works")
    page = html_of(search(client, "carburetor"))
    texts = ("Carburetor kit", "Carburetor fair", "Carburetor tuning guide", "Carburetor Works")
    positions = [page.index(text) for text in texts]
    assert positions == sorted(positions)


def test_the_search_answers_on_a_language_home_too(client: Client) -> None:
    language = other_language()
    listed("Valvola speciale", language=language)
    page = html_of(search(client, "valvola", language))
    assert "Valvola speciale" in page
    assert f'lang="{language}"' in page


def test_a_search_in_one_language_finds_that_languages_texts(client: Client) -> None:
    listed("Valvola speciale", language=other_language())
    assert "Valvola speciale" not in html_of(search(client, "valvola"))


# --- SKU, GTIN, MPN, partial SKU, variant SKU, typo


def test_a_sku_query_finds_the_product(client: Client) -> None:
    listed("Plain thing", sku="ZX-4412")
    assert "Plain thing" in html_of(search(client, "ZX-4412"))


def test_a_partial_sku_finds_the_product(client: Client) -> None:
    listed("Plain thing", sku="QWERTY-9001")
    assert "Plain thing" in html_of(search(client, "QWERTY-9"))


def test_a_variant_sku_finds_its_parent(client: Client) -> None:
    product = listed("Parent thing", sku="PARENT-1")
    VariantFactory.create(product=product, sku="VARIANT-77")
    assert "Parent thing" in html_of(search(client, "VARIANT-77"))


def test_a_gtin_finds_the_product(client: Client) -> None:
    listed("Other thing")
    listed("Barcode thing", gtin="4006381333931")
    page = html_of(search(client, "4006381333931"))
    assert "Barcode thing" in page
    assert "Other thing" not in page


def test_an_mpn_finds_the_product(client: Client) -> None:
    listed("Part thing", mpn="MPN-5521/B")
    assert "Part thing" in html_of(search(client, "mpn-5521/b"))


def test_a_gtin_of_a_draft_product_finds_nothing(client: Client) -> None:
    listed("Draft barcode", gtin="4006381333931", status=Product.Status.DRAFT)
    assert "Draft barcode" not in html_of(search(client, "4006381333931"))


def test_a_typo_finds_the_product_through_the_fallback(client: Client) -> None:
    listed("Carburetor")
    assert "Carburetor" in html_of(search(client, "Carburator"))


# --- Never drafts, hidden, retired or suspended vendors


def test_only_public_items_appear(client: Client) -> None:
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    listed("Zebra visible")
    listed("Zebra draft", status=Product.Status.DRAFT)
    listed("Zebra retired", status=Product.Status.RETIRED)
    listed("Zebra suspended", vendor=suspended)
    public_event("Zebra open day")
    public_event("Zebra hidden day", hidden_at=timezone.now())
    EventPageFactory.create(title="Zebra draft day", live=False)
    public_event("Zebra suspended day", vendor=suspended)
    BlogPostPageFactory.create(title="Zebra live article")
    BlogPostPageFactory.create(title="Zebra draft article", live=False)
    active_vendor(display_name="Zebra Active", slug="zebra-active")
    VendorFactory.create(
        status=Vendor.Status.SUSPENDED, display_name="Zebra Suspended", slug="zebra-suspended"
    )
    page = html_of(search(client, "zebra"))
    for shown in ("visible", "open day", "live article", "Active"):
        assert f"Zebra {shown}" in page
    for hidden in ("draft", "retired", "suspended", "hidden day", "Suspended"):
        assert f"Zebra {hidden}" not in page


# --- The page itself


def test_the_page_is_noindex_follow_and_never_cached(client: Client) -> None:
    listed("Anything goes")
    response = search(client, "anything")
    assert NOINDEX_FOLLOW in html_of(response)
    assert "no-store" in response["Cache-Control"]
    assert "private" in response["Cache-Control"]


@pytest.mark.parametrize("query", ["", "   ", "nomatchatall"])
def test_every_outcome_is_noindex_follow(client: Client, query: str) -> None:
    assert NOINDEX_FOLLOW in html_of(search(client, query))


def test_the_form_is_a_plain_get_form(client: Client) -> None:
    page = html_of(search(client, "x1"))
    form = re.search(r'<form[^>]*role="search"[^>]*>', page)
    assert form is not None
    assert 'method="get"' in form.group(0)
    assert re.search(r'<input[^>]*name="s"', page)


def test_an_empty_query_shows_the_form_and_no_results(client: Client) -> None:
    listed("Anything goes")
    page = html_of(search(client, ""))
    assert re.search(r'<input[^>]*name="s"', page)
    assert "Anything goes" not in page


def test_no_match_suggests_categories(client: Client) -> None:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category,
        language=primary_language(),
        name="Gaskets",
        slug="gaskets",
        status=PUBLISHED,
    )
    listed("Anything goes", category=category)
    page = html_of(search(client, "qqqqzzzz"))
    assert "Gaskets" in page
    assert "Anything goes" not in page


def test_the_query_is_length_limited(client: Client) -> None:
    page = html_of(search(client, "a" * 5000))
    value = re.search(r'<input[^>]*name="s"[^>]*value="([^"]*)"', page)
    assert value is not None
    assert len(value.group(1)) <= search_page.QUERY_MAX_LENGTH


def test_the_query_is_escaped(client: Client) -> None:
    page = html_of(search(client, "<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in page


# --- Filters and pagination (CAT.8)


def test_listing_filters_and_numbered_pagination_apply(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "LISTING_PER_PAGE", 2)
    vendor = active_vendor(sells_online=True)
    for number in range(1, 6):
        listed(f"Gadget {number}", vendor=vendor, price_net=Decimal(10 * number))
    page = html_of(search(client, "gadget"))
    assert 'class="pagination"' in page
    assert re.search(r'href="[^"]*s=gadget[^"]*page=2', page)
    assert html_of(search(client, "gadget", page="3")).count("product-card-title") == 1
    assert search(client, "gadget", page="9").status_code == 404
    cheap = html_of(search(client, "gadget", price_max="20"))
    assert cheap.count("product-card-title") == 2


def test_see_all_shows_one_group_only(client: Client) -> None:
    listed("Mango product")
    public_event("Mango fair")
    page = html_of(search(client, "mango", **{"in": "events"}))
    assert "Mango fair" in page
    assert "Mango product" not in page


# --- Cost and abuse


def test_the_query_count_is_bounded_whatever_the_catalogue_size(client: Client) -> None:
    vendor = active_vendor()
    listed("Bolt one", vendor=vendor)
    with CaptureQueriesContext(connection) as small:
        html_of(search(client, "bolt"))
    for number in range(2, 12):
        listed(f"Bolt {number}", vendor=vendor)
        public_event(f"Bolt fair {number}")
    with CaptureQueriesContext(connection) as large:
        html_of(search(client, "bolt"))
    assert len(large) <= len(small) + 4
    assert len(large) <= 60


def test_the_rate_limit_answers_429(client: Client, settings: Any) -> None:
    settings.SEARCH_RATE_LIMIT = 2
    token = secrets.token_urlsafe(6)
    assert search(client, token).status_code == 200
    assert search(client, token).status_code == 200
    blocked = search(client, token)
    assert blocked.status_code == 429
    assert int(blocked["Retry-After"]) >= 1


def test_an_empty_query_costs_no_rate_limit_slot(client: Client, settings: Any) -> None:
    settings.SEARCH_RATE_LIMIT = 1
    for _ in range(3):
        assert search(client, "").status_code == 200


def test_the_home_without_a_query_is_still_the_home(client: Client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "no-store" not in response.get("Cache-Control", "")

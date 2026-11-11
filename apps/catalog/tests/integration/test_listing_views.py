"""CAT.8 (part 2/3): listing pages and their URLs (spec 03 §3.2 URLs, D-07; spec 09 fallback).

Category pages live on `/product-category/{path}/` (`/it/categoria-prodotto/{path}/`), taxonomy
term pages on `/{taxonomy}/{term}/` and `/{taxonomy}/{term}/{child}/` with the slugs of the page
language, brand pages on `/brand/{slug}/` (`/it/marchio/{slug}/`) and vendor storefronts on
`/shop/{vendor}/`. Every configured language answers: a language without a published text shows
the EN fallback with an EN canonical, and `hreflang` lists only published languages.

A request in another language's slugs, or with a query that is not canonical, gets a single 301
to the canonical URL; filtered and paginated URLs build on the page's current URL. A page past
the last one and an unknown filter value answer 404.
"""

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from wagtail.models import Page, Site

from apps.catalog import listings
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.core.i18n import configured_languages
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED
INDEX = "index, follow"
NOINDEX = "noindex, follow"


def prefix(language: str) -> str:
    return "" if language == settings.LANGUAGE_CODE else f"/{language}"


def listed(**kwargs: Any) -> Product:
    kwargs.setdefault("vendor", VendorFactory.create(status=Vendor.Status.ACTIVE))
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    return ProductFactory.create(**kwargs)


@pytest.fixture
def compat() -> dict[str, Any]:
    """A landing taxonomy "compatibility" (IT "compatibilita") with "maker" > "model" terms,
    published in EN and IT, and one product on the child term."""
    taxonomy = TaxonomyFactory.create(code="compat", has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", slug="compatibility", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", slug="compatibilita", status=PUBLISHED
    )
    maker = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=maker, language="en", slug="maker", status=PUBLISHED)
    TermTranslationFactory.create(term=maker, language="it", slug="marca", status=PUBLISHED)
    model = TermFactory.create(parent=maker)
    TermTranslationFactory.create(term=model, language="en", slug="model", status=PUBLISHED)
    TermTranslationFactory.create(term=model, language="it", slug="modello", status=PUBLISHED)
    product = listed()
    product.terms.add(model)
    return {"taxonomy": taxonomy, "maker": maker, "model": model, "product": product}


def cms_page(parent: Page, slug: str) -> Page:
    """A live CMS standard page under `parent` (no import of the cms app: same layer)."""
    standard_page = apps.get_model("cms", "StandardPage")
    page: Page = parent.add_child(instance=standard_page(title=slug.title(), slug=slug))
    return page


def cms_home() -> Page:
    home: Page = Site.objects.get(is_default_site=True).root_page
    return home


def assert_redirect(response: Any, location: str) -> None:
    assert response.status_code == 301, response.status_code
    assert response["Location"] == location


# --- Taxonomy term pages (D-07)


@pytest.mark.parametrize("language", configured_languages())
def test_a_term_page_answers_200_in_every_configured_language(
    client: Client, compat: dict[str, Any], language: str
) -> None:
    path = (
        "/it/compatibilita/marca/"
        if language == "it"
        else f"{prefix(language)}/compatibility/maker/"
    )
    response = client.get(path)
    assert response.status_code == 200
    assert [p.pk for p in response.context["products"]] == [compat["product"].pk]


def test_a_child_term_page_lists_its_subtree_only(client: Client, compat: dict[str, Any]) -> None:
    sibling = TermFactory.create(parent=compat["maker"])
    TermTranslationFactory.create(term=sibling, language="en", slug="other", status=PUBLISHED)
    listed().terms.add(sibling)
    response = client.get("/compatibility/maker/model/")
    assert response.status_code == 200
    assert [p.pk for p in response.context["products"]] == [compat["product"].pk]
    assert client.get("/it/compatibilita/marca/modello/").status_code == 200


def test_other_language_slugs_redirect_once_to_the_page_language(
    client: Client, compat: dict[str, Any]
) -> None:
    assert_redirect(
        client.get("/it/compatibility/maker/model/"), "/it/compatibilita/marca/modello/"
    )
    assert_redirect(client.get("/compatibilita/marca/"), "/compatibility/maker/")
    # Query kept, and made canonical in the same hop.
    assert_redirect(
        client.get("/it/compatibility/maker/?page=1&utm_source=x"),
        "/it/compatibilita/marca/?utm_source=x",
    )


def test_a_language_without_a_published_text_uses_the_en_slugs(
    client: Client, compat: dict[str, Any]
) -> None:
    response = client.get("/de/compatibility/maker/")
    assert response.status_code == 200
    assert response.context["is_fallback"] is True
    assert response.context["canonical_url"] == "http://testserver/compatibility/maker/"
    assert_redirect(client.get("/de/compatibilita/marca/"), "/de/compatibility/maker/")


def test_hreflang_lists_only_published_languages(client: Client, compat: dict[str, Any]) -> None:
    response = client.get("/compatibility/maker/")
    alternates = dict(response.context["alternates"])
    assert alternates == {
        "en": "http://testserver/compatibility/maker/",
        "it": "http://testserver/it/compatibilita/marca/",
        "x-default": "http://testserver/compatibility/maker/",
    }
    assert response.context["canonical_url"] == "http://testserver/compatibility/maker/"
    assert response.context["is_fallback"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/compatibility/nothing/",
        "/compatibility/maker/nothing/",
        "/nothing/maker/",
        "/compatibility/model/",  # a child slug is not a top-level term
    ],
)
def test_unknown_taxonomies_and_terms_answer_404(
    client: Client, compat: dict[str, Any], path: str
) -> None:
    assert client.get(path).status_code == 404


def test_a_taxonomy_without_landing_pages_has_no_term_pages(client: Client) -> None:
    taxonomy = TaxonomyFactory.create(has_landing=False)
    TaxonomyTranslationFactory.create(taxonomy=taxonomy, slug="season", status=PUBLISHED)
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=term, slug="summer", status=PUBLISHED)
    assert client.get("/season/summer/").status_code == 404


def test_a_term_without_a_published_text_has_no_page(client: Client) -> None:
    taxonomy = TaxonomyFactory.create(has_landing=True)
    TaxonomyTranslationFactory.create(taxonomy=taxonomy, slug="occasion", status=PUBLISHED)
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=term, slug="wedding", status=TranslationStatus.MACHINE)
    assert client.get("/occasion/wedding/").status_code == 404


# --- Query: canonical form, pagination, filters


def test_a_non_canonical_query_redirects_to_the_canonical_one(
    client: Client, compat: dict[str, Any]
) -> None:
    base = "/compatibility/maker/"
    assert_redirect(client.get(f"{base}?page=1"), base)
    assert_redirect(
        client.get(f"{base}?price_max=20&price_min=5"), f"{base}?price_min=5&price_max=20"
    )
    assert_redirect(
        client.get(f"{base}?price_min=20&price_max=5"), f"{base}?price_min=5&price_max=20"
    )
    assert_redirect(client.get(f"{base}?page=abc"), base)
    assert_redirect(client.get(f"{base}?price_min=5.5"), f"{base}?price_min=5.50")


def test_tracking_parameters_stay_without_a_redirect_and_leave_the_canonical(
    client: Client, compat: dict[str, Any]
) -> None:
    response = client.get("/compatibility/maker/?price_min=5&utm_source=x")
    assert response.status_code == 200
    assert response.context["canonical_url"] == (
        "http://testserver/compatibility/maker/?price_min=5"
    )


def test_a_page_past_the_last_answers_404(client: Client, compat: dict[str, Any]) -> None:
    for _ in range(3):
        listed().terms.add(compat["model"])
    # Four products, 24 per page: page 2 is past the end.
    assert client.get("/it/compatibilita/marca/?page=2").status_code == 404
    response = client.get("/it/compatibilita/marca/")
    assert response.context["window"].page_count == 1


def test_paginated_urls_build_on_the_term_url(
    client: Client, compat: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.catalog import services

    monkeypatch.setattr(services, "LISTING_PER_PAGE", 1)
    listed().terms.add(compat["model"])
    first = client.get("/it/compatibilita/marca/")
    assert first.status_code == 200
    assert first.context["next_url"] == "/it/compatibilita/marca/?page=2"
    assert first.context["previous_url"] is None
    second = client.get("/it/compatibilita/marca/?page=2")
    assert second.status_code == 200
    assert second.context["canonical_url"] == "http://testserver/it/compatibilita/marca/?page=2"
    assert second.context["previous_url"] == "/it/compatibilita/marca/"
    assert second.context["next_url"] is None
    assert client.get("/it/compatibilita/marca/?page=3").status_code == 404


def test_an_unknown_filter_value_answers_404(client: Client, compat: dict[str, Any]) -> None:
    assert client.get("/compatibility/maker/?vendor=nobody").status_code == 404
    assert client.get("/compatibility/maker/?compatibility=nothing").status_code == 404


def test_a_filtered_listing_is_indexable_on_its_canonical_url(
    client: Client, compat: dict[str, Any]
) -> None:
    vendor = compat["product"].vendor
    response = client.get(f"/compatibility/maker/?vendor={vendor.slug}")
    assert response.status_code == 200
    assert response.context["robots"] == INDEX
    assert response.context["canonical_url"] == (
        f"http://testserver/compatibility/maker/?vendor={vendor.slug}"
    )


def test_a_filtered_fallback_page_has_the_en_listing_as_canonical(
    client: Client, compat: dict[str, Any]
) -> None:
    response = client.get("/de/compatibility/maker/?compatibility=maker/model&price_min=1")
    assert response.status_code == 200
    assert response.context["canonical_url"] == (
        "http://testserver/compatibility/maker/?compatibility=maker/model&price_min=1"
    )
    # The IT page re-expresses the filter in IT slugs for its alternate.
    response = client.get("/compatibility/maker/?compatibility=maker/model")
    assert dict(response.context["alternates"])["it"] == (
        "http://testserver/it/compatibilita/marca/?compatibilita=marca/modello"
    )


def test_robots_rules(client: Client, compat: dict[str, Any]) -> None:
    assert client.get("/compatibility/maker/").context["robots"] == INDEX
    empty = client.get("/compatibility/maker/?price_min=1000")
    assert empty.status_code == 200
    assert empty.context["robots"] == NOINDEX  # an empty filtered listing


def test_pages_past_the_indexed_depth_are_noindex(
    client: Client, compat: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.catalog import services

    monkeypatch.setattr(services, "LISTING_PER_PAGE", 1)
    for _ in range(listings.INDEXED_PAGES):
        listed().terms.add(compat["model"])
    last_indexed = client.get(f"/compatibility/maker/?page={listings.INDEXED_PAGES}")
    assert last_indexed.context["robots"] == INDEX
    beyond = client.get(f"/compatibility/maker/?page={listings.INDEXED_PAGES + 1}")
    assert beyond.status_code == 200
    assert beyond.context["robots"] == NOINDEX


def test_the_page_renders_canonical_robots_and_hreflang_tags(
    client: Client, compat: dict[str, Any]
) -> None:
    html = client.get("/compatibility/maker/").content.decode()
    assert '<link rel="canonical" href="http://testserver/compatibility/maker/">' in html
    assert '<meta name="robots" content="index, follow">' in html
    assert (
        '<link rel="alternate" hreflang="it" href="http://testserver/it/compatibilita/marca/">'
        in html
    )


# --- Category, brand and vendor pages


@pytest.fixture
def category() -> dict[str, Any]:
    parent = CategoryFactory.create()
    CategoryTranslationFactory.create(category=parent, slug="bags", status=PUBLISHED)
    CategoryTranslationFactory.create(
        category=parent, language="it", slug="borse", status=PUBLISHED
    )
    child = CategoryFactory.create(parent=parent)
    CategoryTranslationFactory.create(category=child, slug="backpacks", status=PUBLISHED)
    product = listed(category=child)
    listed()  # elsewhere
    return {"parent": parent, "child": child, "product": product}


def test_category_pages_per_language(client: Client, category: dict[str, Any]) -> None:
    response = client.get("/product-category/bags/backpacks/")
    assert response.status_code == 200
    assert [p.pk for p in response.context["products"]] == [category["product"].pk]
    assert client.get("/product-category/bags/").status_code == 200
    # The child has no IT text: its EN slug under the IT parent slug.
    assert client.get("/it/categoria-prodotto/borse/backpacks/").status_code == 200
    assert_redirect(
        client.get("/it/product-category/bags/backpacks/"),
        "/it/categoria-prodotto/borse/backpacks/",
    )
    assert_redirect(client.get("/categoria-prodotto/borse/"), "/product-category/bags/")
    for language in configured_languages():
        if language not in ("en", "it"):
            assert client.get(f"/{language}/product-category/bags/").status_code == 200
    assert client.get("/product-category/nothing/").status_code == 404


def test_brand_pages_per_language(client: Client) -> None:
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    BrandTranslationFactory.create(brand=brand, language="it", slug="acme-it", status=PUBLISHED)
    product = listed(brand=brand)
    listed()
    response = client.get("/brand/acme/")
    assert response.status_code == 200
    assert [p.pk for p in response.context["products"]] == [product.pk]
    assert client.get("/it/marchio/acme-it/").status_code == 200
    assert_redirect(client.get("/it/brand/acme/"), "/it/marchio/acme-it/")
    assert client.get("/de/brand/acme/").status_code == 200
    assert client.get("/brand/nothing/").status_code == 404


def test_vendor_storefronts_per_language(client: Client) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, status=PUBLISHED)
    product = listed(vendor=vendor)
    listed()
    response = client.get(f"/shop/{vendor.slug}/")
    assert response.status_code == 200
    assert [p.pk for p in response.context["products"]] == [product.pk]
    for language in configured_languages():
        assert client.get(f"{prefix(language)}/shop/{vendor.slug}/").status_code == 200
    closed = VendorFactory.create(status=Vendor.Status.CLOSED)
    assert client.get(f"/shop/{closed.slug}/").status_code == 404


def test_the_vendor_storefront_shows_the_logo_with_its_alt_text(client: Client) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.core.audit import AuditContext
    from apps.vendors.services import set_vendor_logo
    from apps.vendors.tests.factories import VendorMemberFactory
    from tests.upload_corpus import png

    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, status=PUBLISHED)
    listed(vendor=vendor)
    assert b"listing-logo" not in client.get(f"/shop/{vendor.slug}/").content
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    set_vendor_logo(
        vendor,
        upload=SimpleUploadedFile("logo.png", png()),
        alt="Acme anvil mark",
        actor=owner,
        ctx=AuditContext(actor_label="user:owner", source="vendor"),
    )
    page = client.get(f"/shop/{vendor.slug}/").text
    assert 'class="listing-logo"' in page
    assert 'alt="Acme anvil mark"' in page


# --- Route conflicts


def test_other_two_segment_paths_still_reach_the_cms(
    client: Client, compat: dict[str, Any]
) -> None:
    # Not a taxonomy slug: the CMS page lookup answers (404 here, no page tree).
    assert client.get("/about/team/").status_code == 404
    assert client.get("/healthz").status_code == 200


def test_a_taxonomy_slug_never_shadows_the_catalog_segments(client: Client) -> None:
    taxonomy = TaxonomyFactory.create(has_landing=True)
    TaxonomyTranslationFactory.create(taxonomy=taxonomy, slug="brand", status=PUBLISHED)
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=term, slug="acme", status=PUBLISHED)
    brand = BrandFactory.create()
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    response = client.get("/brand/acme/")
    assert response.status_code == 200
    assert response.context["scope"].brand == brand


@pytest.mark.parametrize("status", [PUBLISHED, TranslationStatus.MACHINE])
def test_a_cms_page_sharing_its_first_segment_with_a_taxonomy_slug_stays_reachable(
    client: Client, status: str
) -> None:
    guide = cms_page(cms_home(), "guide")
    cms_page(guide, "intro")
    assert client.get("/guide/intro/").status_code == 200
    taxonomy = TaxonomyFactory.create(has_landing=True)
    TaxonomyTranslationFactory.create(taxonomy=taxonomy, slug="guide", status=status)
    assert client.get("/guide/intro/").status_code == 200


def test_a_draft_slug_in_another_language_never_shadows_a_cms_page(client: Client) -> None:
    guida = cms_page(cms_home(), "guida")
    cms_page(guida, "intro")
    taxonomy = TaxonomyFactory.create(has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="it", slug="guida", status=TranslationStatus.MACHINE
    )
    assert client.get("/guida/intro/").status_code == 200


def test_an_unknown_term_under_a_taxonomy_slug_falls_back_to_the_cms(
    client: Client, compat: dict[str, Any]
) -> None:
    # The term page still wins where it exists.
    compatibility = cms_page(cms_home(), "compatibility")
    cms_page(compatibility, "notes")
    assert client.get("/compatibility/notes/").status_code == 200
    assert client.get("/compatibility/maker/").context["scope"].term == compat["maker"]


# --- Other-language redirects keep the filter


def test_an_other_language_redirect_re_expresses_the_filter(
    client: Client, compat: dict[str, Any]
) -> None:
    assert_redirect(
        client.get("/it/compatibility/maker/?compatibility=maker/model&utm_source=x"),
        "/it/compatibilita/marca/?compatibilita=marca/modello&utm_source=x",
    )
    assert_redirect(
        client.get("/compatibilita/marca/?compatibilita=marca/modello"),
        "/compatibility/maker/?compatibility=maker/model",
    )


# --- Query count


def _filter_values(compat: dict[str, Any], first: int, count: int) -> str:
    values = []
    for number in range(first, first + count):
        term = TermFactory.create(taxonomy=compat["taxonomy"])
        TermTranslationFactory.create(
            term=term, language="en", slug=f"value-{number}", status=PUBLISHED
        )
        TermTranslationFactory.create(
            term=term, language="it", slug=f"valore-{number}", status=PUBLISHED
        )
        values.append(f"value-{number}")
    return ",".join(sorted(values))


def _queries(client: Client, path: str) -> int:
    with CaptureQueriesContext(connection) as queries:
        assert client.get(path).status_code == 200
    return len(queries)


def test_a_filtered_term_page_runs_a_bounded_number_of_queries(
    client: Client, compat: dict[str, Any]
) -> None:
    one = _queries(client, f"/compatibility/maker/?compatibility={_filter_values(compat, 0, 1)}")
    many = _queries(client, f"/compatibility/maker/?compatibility={_filter_values(compat, 1, 12)}")
    assert many == one

"""CAT.20 (3/3): the brand page content and its JSON-LD (spec 03 CAT.20, spec 10 SEO.7).

The brand page is the CAT.8 brand listing. It adds the brand's logo, official website, Wikidata
link, manufacturer and its description in the page language (a missing translation gives no
description, never a fallback), and one JSON-LD `@graph` whose Brand node has the same `@id` for
every vendor and language.
"""

import json
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.apps import apps
from django.test import Client, override_settings
from wagtail.models import Site

from apps.catalog import brands, services
from apps.catalog.models import Brand, Product
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    ManufacturerFactory,
    ProductFactory,
    brand_logo_image,
)
from apps.core.audit import AuditContext
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _site_allows_indexing() -> None:
    """The site has turned its indexing switch on (SEO settings)."""
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


PUBLISHED = "published"
CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def _brand(**fields: Any) -> Brand:
    brand = BrandFactory.create(name="Acme", **fields)
    BrandTranslationFactory.create(
        brand=brand, language="en", slug="acme", description="<p>Anvils since 1949.</p>",
        status=PUBLISHED,
    )  # fmt: skip
    BrandTranslationFactory.create(
        brand=brand, language="it", slug="acme-it", description="<p>Incudini dal 1949.</p>",
        status=PUBLISHED,
    )  # fmt: skip
    return brand


def _page(client: Client, path: str) -> BeautifulSoup:
    response = client.get(path)
    assert response.status_code == 200, path
    return BeautifulSoup(response.content, "html.parser")


def _graph(page: BeautifulSoup) -> list[dict[str, Any]]:
    scripts = page.find_all("script", attrs={"type": "application/ld+json"})
    assert len(scripts) == 1  # SEO.7: exactly one JSON-LD block per page
    data = json.loads(scripts[0].get_text())
    assert data["@context"] == "https://schema.org"
    graph: list[dict[str, Any]] = data["@graph"]
    return graph


def _node(graph: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(node for node in graph if node["@type"] == kind)


# --- Page content


def test_the_page_shows_the_description_in_the_page_language(client: Client) -> None:
    _brand()
    assert "Anvils since 1949." in _page(client, "/brand/acme/").get_text()
    italian = _page(client, "/it/marchio/acme-it/").get_text()
    assert "Incudini dal 1949." in italian
    assert "Anvils since 1949." not in italian


def test_a_missing_translation_gives_no_description_never_a_fallback(client: Client) -> None:
    brand = _brand()
    brand.translations.filter(language="it").update(description="")
    page = _page(client, "/it/marchio/acme-it/")
    assert "Anvils since 1949." not in page.get_text()
    assert page.select_one(".brand-description") is None


def test_an_unpublished_translation_is_not_shown(client: Client) -> None:
    brand = _brand()
    brand.translations.filter(language="it").update(status="reviewed")
    page = _page(client, "/it/marchio/acme/")  # the fallback page uses the source slug
    assert "Incudini dal 1949." not in page.get_text()


def test_the_description_is_sanitised(client: Client) -> None:
    brand = _brand()
    brand.translations.filter(language="en").update(
        description=(
            '<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">l</a>'
        )
    )
    block = _page(client, "/brand/acme/").select_one(".brand-description")
    assert block is not None
    assert block.find("script") is None
    assert "onclick" not in str(block)
    assert "javascript:" not in str(block)


def test_the_page_shows_logo_website_wikidata_and_manufacturer(client: Client) -> None:
    logo = brand_logo_image()
    maker = ManufacturerFactory.create(legal_name="Acme Holdings Ltd", country="GB")
    _brand(
        logo=logo, logo_alt="Acme anvil", url="https://acme.example/", wikidata_id="Q42",
        manufacturer=maker,
    )  # fmt: skip
    page = _page(client, "/brand/acme/")
    image = page.select_one(".brand-profile img")
    assert image is not None
    assert image["alt"] == "Acme anvil"
    site = page.select_one('.brand-profile a[href="https://acme.example/"]')
    assert site is not None
    assert "noopener" in site["rel"]
    wikidata = page.select_one('.brand-profile a[href="https://www.wikidata.org/wiki/Q42"]')
    assert wikidata is not None
    assert "Acme Holdings Ltd" in page.select_one(".brand-profile").get_text()  # type: ignore[union-attr]


def test_a_brand_with_nothing_to_show_has_no_profile_block(client: Client) -> None:
    BrandTranslationFactory.create(
        brand=BrandFactory.create(name="Plain"), slug="plain", description="", status=PUBLISHED
    )
    assert _page(client, "/brand/plain/").select_one(".brand-profile") is None


def test_a_non_https_stored_link_is_never_rendered(client: Client) -> None:
    # Legacy data that skipped validation (a queryset update) must stay inert.
    brand = _brand()
    Brand.objects.filter(pk=brand.pk).update(url="javascript:alert(1)")
    page = _page(client, "/brand/acme/")
    assert "javascript:" not in str(page)


# --- JSON-LD


def test_the_page_has_one_graph_with_the_brand_node(client: Client) -> None:
    maker = ManufacturerFactory.create(legal_name="Acme Holdings Ltd", country="GB")
    _brand(
        wikidata_id="Q42", url="https://acme.example/",
        same_as=["https://en.wikipedia.org/wiki/Acme"], manufacturer=maker,
    )  # fmt: skip
    graph = _graph(_page(client, "/brand/acme/"))
    node = _node(graph, "Brand")
    assert node["name"] == "Acme"
    assert node["@id"] == "http://testserver/brand/acme/#brand"
    assert node["url"] == "https://acme.example/"
    assert node["description"] == "Anvils since 1949."
    assert node["sameAs"] == [
        "https://www.wikidata.org/wiki/Q42",
        "https://en.wikipedia.org/wiki/Acme",
    ]
    org = _node(graph, "Organization")
    assert node["manufacturer"] == {"@id": org["@id"]}
    assert org["brand"] == [{"@id": node["@id"]}]
    assert org["legalName"] == "Acme Holdings Ltd"


def test_the_brand_id_is_the_same_in_every_language(client: Client) -> None:
    _brand()
    english = _node(_graph(_page(client, "/brand/acme/")), "Brand")
    italian = _node(_graph(_page(client, "/it/marchio/acme-it/")), "Brand")
    assert english["@id"] == italian["@id"]
    assert english["description"] == "Anvils since 1949."
    assert italian["description"] == "Incudini dal 1949."


def test_the_node_has_no_description_for_a_missing_translation(client: Client) -> None:
    brand = _brand()
    brand.translations.filter(language="it").update(description="")
    node = _node(_graph(_page(client, "/it/marchio/acme-it/")), "Brand")
    assert "description" not in node


def test_two_vendors_selling_the_brand_share_one_node(client: Client) -> None:
    brand = _brand(wikidata_id="Q42")
    ProductFactory.create(brand=brand, vendor=VendorFactory.create())
    ProductFactory.create(brand=brand, vendor=VendorFactory.create())
    page_id = _node(_graph(_page(client, "/brand/acme/")), "Brand")["@id"]
    # What the product pages (SEO.1) reference: one id per brand, whoever sells the product.
    products = Product.objects.filter(brand=brand).select_related("brand")
    assert products.count() == 2
    ids = {
        services.brand_node_id(product.brand, site="http://testserver")
        for product in products
        if product.brand is not None
    }
    assert ids == {page_id}


def test_a_hostile_brand_name_is_inert_in_the_json_ld(client: Client) -> None:
    brand = _brand()
    Brand.objects.filter(pk=brand.pk).update(name="</script><img src=x onerror=alert(1)>")
    page = _page(client, "/brand/acme/")
    assert page.find("img", attrs={"src": "x"}) is None
    node = _node(_graph(page), "Brand")
    assert node["name"] == "</script><img src=x onerror=alert(1)>"


def test_a_stored_non_https_link_is_not_in_the_graph(client: Client) -> None:
    brand = _brand()
    Brand.objects.filter(pk=brand.pk).update(
        url="http://acme.example/", same_as=["http://x.example/", "https://ok.example/"]
    )
    node = _node(_graph(_page(client, "/brand/acme/")), "Brand")
    assert "url" not in node
    assert node["sameAs"] == ["https://ok.example/"]


def test_the_logo_is_in_the_node(client: Client) -> None:
    logo = brand_logo_image()
    _brand(logo=logo, logo_alt="Acme anvil")
    node = _node(_graph(_page(client, "/brand/acme/")), "Brand")
    assert node["logo"].startswith("http://testserver/")


# --- Visibility, indexing and redirects


def test_a_pending_brand_has_no_page_and_no_graph(client: Client) -> None:
    pending = brands.propose_brand(VendorFactory.create(), "Newcomer", ctx=CTX).brand
    assert pending is not None
    BrandTranslationFactory.create(brand=pending, slug="newcomer", status=PUBLISHED)
    assert client.get("/brand/newcomer/").status_code == 404


@override_settings(FORCE_NOINDEX=True)
def test_a_forced_noindex_brand_page_is_noindex(client: Client) -> None:
    _brand()
    response = client.get("/brand/acme/")
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert b"noindex" in response.content


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_not_forced_a_brand_page_is_indexable(client: Client) -> None:
    _brand()
    response = client.get("/brand/acme/")
    assert "X-Robots-Tag" not in response
    assert b"noindex" not in response.content


def test_a_merged_brand_redirects_in_one_hop_to_the_final_brand(client: Client) -> None:
    first, second, third = _brand(), BrandFactory.create(name="Acme B"), BrandFactory.create(
        name="Acme C"
    )  # fmt: skip
    for brand, slug in ((second, "acme-b"), (third, "acme-c")):
        BrandTranslationFactory.create(brand=brand, language="en", slug=slug, status=PUBLISHED)
    brands.merge_brands(first, second, ctx=CTX)
    brands.merge_brands(second, third, ctx=CTX)
    response = client.get("/brand/acme/")
    assert response.status_code == 301
    assert response["Location"] == "/brand/acme-c/"
    assert client.get("/brand/acme-b/").status_code == 301
    assert client.get("/brand/acme-c/").status_code == 200

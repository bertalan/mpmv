"""CMS.10: the product grid block (spec 10 §10.2, spec 03 CAT.8 listings).

Done when: "An editor places a product grid filtered by a term on a page and it renders the CAT.8
results". The grid shows, newest first and up to its limit, the products the CAT.8 listing query
gives for its category, term, brand or vendor (`listing_products`), as the listing's product
cards. Unpublished products and products of vendors that are not active never show. An optional
"See all" link points to the canonical CAT.8 listing page in the page's language. A grid with
no product is left out. The heading is a translatable segment (I18N.3); the block renders with a
number of queries that does not grow with the products it shows.
"""

from decimal import Decimal
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from wagtail_localize.models import StringSegment, TranslationSource

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog import services as catalog
from apps.catalog.models import Brand, Category, Product, Term
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.cms.blocks import ProductGridBlock
from apps.cms.models import HomePage, LegalPage, StandardPage
from apps.cms.services import SeedReport
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation on commit; a test transaction never commits."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _home(code: str = "en") -> HomePage:
    home: HomePage = HomePage.objects.get(locale__language_code=code)
    return home


def _about(code: str = "en") -> StandardPage:
    about: StandardPage = StandardPage.objects.get(
        locale__language_code=code, depth=_home(code).depth + 1
    )
    return about


def _grid(
    kind: str, target: Any, *, heading: str = "Our picks", limit: int = 8, see_all: bool = True
) -> tuple[str, dict[str, Any]]:
    return (
        "product_grid",
        {"heading": heading, "source": [(kind, target)], "limit": limit, "see_all": see_all},
    )


def _publish(page: Any, body: list[tuple[str, Any]]) -> BeautifulSoup:
    page.body = body
    page.save_revision().publish()
    response = Client().get(page.url)
    assert response.status_code == 200
    return BeautifulSoup(response.content, "html.parser")


def _grid_section(html: BeautifulSoup) -> Any:
    return html.find("section", class_="block-product-grid")


def _titles(html: BeautifulSoup) -> list[str]:
    section = _grid_section(html)
    assert section is not None
    return [h.get_text(strip=True) for h in section.find_all(class_="product-card-title")]


_counter = iter(range(10_000))


def listed(title: str, **kwargs: Any) -> Product:
    """A published product of an active vendor with EN and IT texts."""
    kwargs.setdefault("vendor", VendorFactory.create(status=Vendor.Status.ACTIVE))
    kwargs.setdefault("status", Product.Status.PUBLISHED)
    product: Product = ProductFactory.create(**kwargs)
    slug = f"{title.lower().replace(' ', '-')}-{next(_counter)}"
    ProductTranslationFactory.create(
        product=product, title=title, slug=f"{slug}-en", status=PUBLISHED
    )
    ProductTranslationFactory.create(
        product=product, language="it", title=f"{title} IT", slug=f"{slug}-it", status=PUBLISHED
    )
    return product


@pytest.fixture
def maker() -> Term:
    """A term of a taxonomy with term pages: EN `/compatibility/maker/`, IT
    `/it/compatibilita/marca/`."""
    compat = TaxonomyFactory.create(code="compat", has_landing=True, filterable=False)
    TaxonomyTranslationFactory.create(
        taxonomy=compat, name="Compatibility", slug="compatibility", status=PUBLISHED
    )
    TaxonomyTranslationFactory.create(
        taxonomy=compat, language="it", name="Compatibilità", slug="compatibilita",
        status=PUBLISHED,
    )  # fmt: skip
    term: Term = TermFactory.create(taxonomy=compat)
    TermTranslationFactory.create(term=term, name="Maker", slug="maker", status=PUBLISHED)
    TermTranslationFactory.create(
        term=term, language="it", name="Marca", slug="marca", status=PUBLISHED
    )
    return term


@pytest.fixture
def editor() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


# --- The term grid (the "done when")


def test_a_term_grid_renders_the_cat8_results(seeded: SeedReport, maker: Term) -> None:
    older = listed("Steel bag", price_net=Decimal("12.50"))
    newer = listed("Leather bag")
    other = listed("Unrelated cup")
    older.terms.add(maker)
    newer.terms.add(maker)
    html = _publish(_home(), [_grid("term", maker)])
    expected = catalog.listing_page(
        catalog.ListingScope(term=maker), catalog.listings.ListingParams(), "en"
    ).products
    assert [p.pk for p in expected] == [newer.pk, older.pk]
    assert _titles(html) == ["Leather bag", "Steel bag"]
    assert "Unrelated cup" not in str(_grid_section(html))
    assert other.pk not in [p.pk for p in expected]
    section = _grid_section(html)
    assert section.find("h2").get_text(strip=True) == "Our picks"
    assert section.find("data", value="12.50") is not None
    links = [a["href"] for a in section.find_all("a")]
    assert any(href.endswith("-en/") and "/product/" in href for href in links)


def test_a_term_grid_includes_the_term_subtree(seeded: SeedReport, maker: Term) -> None:
    child = TermFactory.create(parent=maker)
    TermTranslationFactory.create(term=child, name="Model", slug="model", status=PUBLISHED)
    listed("Child bag").terms.add(child)
    assert _titles(_publish(_home(), [_grid("term", maker)])) == ["Child bag"]


def test_the_grid_follows_the_page_language(seeded: SeedReport, maker: Term) -> None:
    listed("Steel bag").terms.add(maker)
    html = _publish(_home("it"), [_grid("term", maker, heading="Scelti per te")])
    assert _titles(html) == ["Steel bag IT"]
    see_all = _grid_section(html).find("a", class_="product-grid-all")
    assert see_all["href"] == "/it/compatibilita/marca/"


def test_the_grid_shows_at_most_its_limit(seeded: SeedReport, maker: Term) -> None:
    for n in range(5):
        listed(f"Bag {n}").terms.add(maker)
    assert _titles(_publish(_home(), [_grid("term", maker, limit=3)])) == [
        "Bag 4",
        "Bag 3",
        "Bag 2",
    ]


def test_see_all_links_to_the_canonical_listing(seeded: SeedReport, maker: Term) -> None:
    listed("Steel bag").terms.add(maker)
    html = _publish(_home(), [_grid("term", maker)])
    see_all = _grid_section(html).find("a", class_="product-grid-all")
    assert see_all["href"] == "/compatibility/maker/"
    assert see_all.get_text(strip=True) == "See all"
    assert Client().get(see_all["href"]).status_code == 200


def test_see_all_is_optional(seeded: SeedReport, maker: Term) -> None:
    listed("Steel bag").terms.add(maker)
    html = _publish(_home(), [_grid("term", maker, see_all=False)])
    assert _grid_section(html).find("a", class_="product-grid-all") is None


def test_a_term_without_term_pages_has_no_see_all_link(seeded: SeedReport) -> None:
    colour = TaxonomyFactory.create(code="colour", has_landing=False)
    TaxonomyTranslationFactory.create(
        taxonomy=colour, name="Colour", slug="colour", status=PUBLISHED
    )
    red = TermFactory.create(taxonomy=colour)
    TermTranslationFactory.create(term=red, name="Red", slug="red", status=PUBLISHED)
    listed("Red bag").terms.add(red)
    html = _publish(_home(), [_grid("term", red)])
    assert _titles(html) == ["Red bag"]
    assert _grid_section(html).find("a", class_="product-grid-all") is None


# --- What never shows


def test_unpublished_products_and_inactive_vendors_never_show(
    seeded: SeedReport, maker: Term
) -> None:
    listed("Public bag").terms.add(maker)
    listed("Draft bag", status=Product.Status.DRAFT).terms.add(maker)
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    listed("Suspended bag", vendor=suspended).terms.add(maker)
    assert _titles(_publish(_home(), [_grid("term", maker)])) == ["Public bag"]


def test_a_grid_without_products_is_left_out(seeded: SeedReport, maker: Term) -> None:
    listed("Draft bag", status=Product.Status.DRAFT).terms.add(maker)
    html = _publish(_home(), [_grid("term", maker, heading="Nothing here")])
    assert _grid_section(html) is None
    assert "Nothing here" not in str(html)


def test_a_vendor_grid_of_a_vendor_no_longer_active_is_left_out(seeded: SeedReport) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=vendor)
    home = _home()
    home.body = [_grid("vendor", vendor)]
    home.save_revision().publish()
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    html = BeautifulSoup(Client().get(home.url).content, "html.parser")
    assert _grid_section(html) is None


# --- Other scopes


def test_a_category_grid(seeded: SeedReport) -> None:
    bags: Category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, name="Bags", slug="bags", status=PUBLISHED)
    listed("Steel bag", category=bags)
    listed("Cup")
    html = _publish(_home(), [_grid("category", bags)])
    assert _titles(html) == ["Steel bag"]
    see_all = _grid_section(html).find("a", class_="product-grid-all")
    assert see_all["href"] == "/product-category/bags/"


def test_a_brand_grid(seeded: SeedReport) -> None:
    brand: Brand = BrandFactory.create(name="Ferro")
    BrandTranslationFactory.create(brand=brand, slug="ferro", status=PUBLISHED)
    listed("Steel bag", brand=brand)
    listed("Cup")
    html = _publish(_home(), [_grid("brand", brand)])
    assert _titles(html) == ["Steel bag"]
    see_all = _grid_section(html).find("a", class_="product-grid-all")
    assert see_all["href"] == "/brand/ferro/"


def test_a_vendor_grid(seeded: SeedReport) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=vendor)
    listed("Cup")
    html = _publish(_about(), [_grid("vendor", vendor)])
    assert _titles(html) == ["Acme bag"]
    see_all = _grid_section(html).find("a", class_="product-grid-all")
    assert see_all["href"] == "/shop/acme/"


# --- The block's rules


def test_the_limit_is_bounded(seeded: SeedReport, maker: Term) -> None:
    block = ProductGridBlock()
    for limit in (0, 25):
        value = block.to_python(
            {"heading": "", "source": [{"type": "term", "value": maker.pk}], "limit": limit}
        )
        with pytest.raises(ValidationError):
            block.clean(value)
    ok = block.to_python(
        {"heading": "", "source": [{"type": "term", "value": maker.pk}], "limit": 24}
    )
    block.clean(ok)


def test_the_grid_needs_exactly_one_source(seeded: SeedReport, maker: Term) -> None:
    block = ProductGridBlock()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    for source in (
        [],
        [{"type": "term", "value": maker.pk}, {"type": "vendor", "value": vendor.pk}],
    ):
        value = block.to_python({"heading": "", "source": source, "limit": 8})
        with pytest.raises(ValidationError):
            block.clean(value)


def test_the_grid_is_offered_on_home_and_standard_pages_only() -> None:
    for model in (HomePage, StandardPage):
        assert "product_grid" in model._meta.get_field("body").stream_block.child_blocks
    assert "product_grid" not in LegalPage._meta.get_field("body").stream_block.child_blocks


def test_the_editor_offers_the_product_grid_block(
    client: Client, seeded: SeedReport, editor: User
) -> None:
    verified_login(client, editor)
    response = client.get(reverse("wagtailadmin_pages:edit", args=(_home().pk,)))
    assert response.status_code == 200
    html = response.content.decode()
    for label in ("Product grid", "Category", "Brand"):
        assert label in html, label


def test_the_category_and_brand_choosers_list_them(
    client: Client, seeded: SeedReport, editor: User
) -> None:
    bags = CategoryFactory.create()
    CategoryTranslationFactory.create(category=bags, name="Bags", slug="bags", status=PUBLISHED)
    BrandFactory.create(name="Ferro")
    verified_login(client, editor)
    for url_name, label in (
        ("cms_category_chooser:choose", "Bags"),
        ("cms_brand_chooser:choose", "Ferro"),
    ):
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        assert label in response.json()["html"]


def test_the_heading_is_a_translatable_segment(seeded: SeedReport, maker: Term) -> None:
    home = _home()
    home.body = [_grid("term", maker, heading="Our picks for you")]
    home.save_revision().publish()
    source, _created = TranslationSource.get_or_create_from_instance(home)
    texts = set(StringSegment.objects.filter(source=source).values_list("string__data", flat=True))
    assert "Our picks for you" in texts


# --- No N+1


def _landing_term(n: int) -> Term:
    taxonomy = TaxonomyFactory.create(code=f"bulk-{n}", has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, name=f"Tax {n}", slug=f"tax-{n}", status=PUBLISHED
    )
    term: Term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(term=term, name=f"Term {n}", slug=f"term-{n}", status=PUBLISHED)
    return term


def _queries_to_render(count: int, term: Term) -> int:
    for n in range(count):
        vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
        listed(f"Bag {n}", vendor=vendor, price_net=Decimal(n + 1)).terms.add(term)
    home = _home()
    home.body = [_grid("term", term, limit=24)]
    home.save_revision().publish()
    client = Client()
    client.get(home.url)  # warm caches (site root paths, locales)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(home.url)
    assert len(_titles(BeautifulSoup(response.content, "html.parser"))) == count
    return len(queries)


def test_the_grid_renders_with_a_constant_number_of_queries(seeded: SeedReport) -> None:
    one = _queries_to_render(1, _landing_term(1))
    many = _queries_to_render(12, _landing_term(2))
    assert many <= one

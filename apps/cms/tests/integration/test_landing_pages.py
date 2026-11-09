"""CMS.4: term and vendor landing pages (spec 10 §10.2, D-07, CAT.8).

Done when: "A term page with its products".

A `TermLandingPage` is the editorial part of a term page. The term page lives on its D-07
listing URL (`/{taxonomy}/{term}/`, served by CAT.8), so the landing page adds no second URL:
its intro, the term's products by category and its related articles show on the listing's first
page, under the listing's own canonical and hreflang. The landing page's URL is the term URL, and
its place in the page tree answers a single 301 to it.

A `VendorLandingPage` tells a vendor's story beyond the storefront: its own page with a story,
images and the vendor's featured products, linked from the storefront. Only active vendors and
published products show.
"""

import re
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.test import Client
from wagtail.models import Locale

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Category, Product, Term
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
    ProductTranslationFactory,
    TaxonomyFactory,
    TaxonomyTranslationFactory,
    TermFactory,
    TermTranslationFactory,
)
from apps.cms.models import HomePage, StandardPage, TermLandingPage, VendorLandingPage
from apps.cms.services import SeedReport
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PUBLISHED = TranslationStatus.PUBLISHED


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _html(path: str) -> Any:  # BeautifulSoup, untyped lookups
    response = Client().get(path)
    assert response.status_code == 200, (path, response.status_code)
    return BeautifulSoup(response.content, "html.parser")


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


def _category(name: str, slug: str, it_name: str, it_slug: str) -> Category:
    category: Category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=category, name=name, slug=slug, status=PUBLISHED)
    CategoryTranslationFactory.create(
        category=category, language="it", name=it_name, slug=it_slug, status=PUBLISHED
    )
    return category


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
def bags() -> Category:
    return _category("Bags", "bags", "Borse", "borse")


@pytest.fixture
def cups() -> Category:
    return _category("Cups", "cups", "Tazze", "tazze")


def _intro(text: str) -> list[tuple[str, Any]]:
    return [("paragraph", f"<p>{text}</p>")]


def term_landing(term: Term, *, parent: Any = None, publish: bool = True, **fields: Any) -> Any:
    fields.setdefault("title", "Maker guide")
    fields.setdefault("slug", "maker-guide")
    fields.setdefault("body", _intro("Everything about the maker."))
    page = TermLandingPage(term=term, live=publish, **fields)
    (parent or _home()).add_child(instance=page)
    if publish:
        page.save_revision().publish()
    return page


def vendor_landing(vendor: Vendor, *, parent: Any = None, **fields: Any) -> Any:
    fields.setdefault("title", "Our workshop")
    fields.setdefault("slug", "our-workshop")
    fields.setdefault("body", _intro("Three generations of craft."))
    page = VendorLandingPage(vendor=vendor, **fields)
    (parent or _home()).add_child(instance=page)
    page.save_revision().publish()
    return page


def _translate(page: Any, code: str, **fields: Any) -> Any:
    copy = page.copy_for_translation(Locale.objects.get(language_code=code))
    for name, value in fields.items():
        setattr(copy, name, value)
    copy.save_revision().publish()
    return copy


def _editorial(html: BeautifulSoup) -> Any:
    return html.find(class_="term-landing")


def _titles(node: Any) -> list[str]:
    return [h.get_text(strip=True) for h in node.find_all(class_="product-tile__name")]


# --- The term page with its products (the "done when")


def test_a_term_page_shows_its_editorial_intro_and_its_products(
    seeded: SeedReport, maker: Term, bags: Category
) -> None:
    listed("Steel bag", category=bags).terms.add(maker)
    listed("Unrelated cup")
    term_landing(maker)
    html = _html("/compatibility/maker/")
    editorial = _editorial(html)
    assert editorial is not None
    assert "Everything about the maker." in editorial.get_text()
    assert html.find("h1").get_text(strip=True) == "Maker"
    results = html.find(class_="listing-results")
    assert "Steel bag" in results.get_text()
    assert "Unrelated cup" not in html.get_text()


def test_the_term_products_show_by_category(
    seeded: SeedReport, maker: Term, bags: Category, cups: Category
) -> None:
    listed("Steel bag", category=bags).terms.add(maker)
    listed("Leather bag", category=bags).terms.add(maker)
    listed("Steel cup", category=cups).terms.add(maker)
    listed("Other bag", category=bags)  # not of the term
    term_landing(maker, category_products=4)
    sections = _editorial(_html("/compatibility/maker/")).select("section.term-landing-category")
    assert [s.find(["h2", "h3"]).get_text(strip=True) for s in sections] == ["Bags", "Cups"]
    assert _titles(sections[0]) == ["Leather bag", "Steel bag"]
    assert _titles(sections[1]) == ["Steel cup"]
    see_all = sections[0].find("a", class_="term-landing-see-all")
    assert see_all["href"] == "/compatibility/maker/?category=bags"
    filtered = _html(see_all["href"])
    cards = filtered.find(class_="listing-results").find_all(class_="product-card-title")
    assert [h.get_text(strip=True) for h in cards] == ["Leather bag", "Steel bag"]


def test_the_by_category_sections_respect_their_limit(
    seeded: SeedReport, maker: Term, bags: Category, cups: Category
) -> None:
    for n in range(4):
        listed(f"Bag {n}", category=bags).terms.add(maker)
    listed("Cup", category=cups).terms.add(maker)
    term_landing(maker, category_products=2)
    sections = _editorial(_html("/compatibility/maker/")).select("section.term-landing-category")
    assert _titles(sections[0]) == ["Bag 3", "Bag 2"]


def test_a_single_category_or_a_zero_limit_shows_no_category_sections(
    seeded: SeedReport, maker: Term, bags: Category, cups: Category
) -> None:
    """One category would repeat the listing below; 0 turns the sections off."""
    listed("Steel bag", category=bags).terms.add(maker)
    page = term_landing(maker)
    assert not _editorial(_html("/compatibility/maker/")).select("section.term-landing-category")
    listed("Steel cup", category=cups).terms.add(maker)
    page.category_products = 0
    page.save_revision().publish()
    assert not _editorial(_html("/compatibility/maker/")).select("section.term-landing-category")


def test_the_category_sections_show_only_public_products(
    seeded: SeedReport, maker: Term, bags: Category, cups: Category
) -> None:
    listed("Public bag", category=bags).terms.add(maker)
    listed("Public cup", category=cups).terms.add(maker)
    listed("Draft bag", category=bags, status=Product.Status.DRAFT).terms.add(maker)
    suspended = VendorFactory.create(status=Vendor.Status.SUSPENDED)
    listed("Suspended bag", category=bags, vendor=suspended).terms.add(maker)
    term_landing(maker)
    text = _editorial(_html("/compatibility/maker/")).get_text()
    assert "Public bag" in text
    assert "Draft bag" not in text
    assert "Suspended bag" not in text


# --- One URL per term page (D-07)


def test_the_landing_page_url_is_the_term_url(seeded: SeedReport, maker: Term) -> None:
    page = term_landing(maker)
    assert page.url == "/compatibility/maker/"
    assert page.get_sitemap_urls() == []


def test_the_page_tree_url_redirects_once_to_the_term_url(seeded: SeedReport, maker: Term) -> None:
    term_landing(maker)
    response = Client().get("/maker-guide/")
    assert response.status_code == 301
    assert response["Location"] == "/compatibility/maker/"


def test_the_term_page_keeps_its_single_canonical(seeded: SeedReport, maker: Term) -> None:
    listed("Steel bag").terms.add(maker)
    term_landing(maker)
    html = _html("/compatibility/maker/")
    canonicals = html.find_all("link", rel="canonical")
    assert [link["href"] for link in canonicals] == ["http://testserver/compatibility/maker/"]


def test_the_seo_title_and_description_apply_to_the_term_page(
    seeded: SeedReport, maker: Term
) -> None:
    term_landing(maker, seo_title="Maker parts and guides", search_description="All maker parts.")
    html = _html("/compatibility/maker/")
    assert html.find("title").get_text().startswith("Maker parts and guides")
    assert html.find("meta", attrs={"name": "description"})["content"] == "All maker parts."


def test_the_editorial_shows_only_on_the_first_unfiltered_page(
    seeded: SeedReport, maker: Term
) -> None:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    listed("Steel bag", vendor=vendor).terms.add(maker)
    term_landing(maker)
    assert _editorial(_html("/compatibility/maker/?vendor=acme")) is None


def test_a_draft_landing_page_does_not_show(seeded: SeedReport, maker: Term) -> None:
    term_landing(maker, publish=False)
    assert _editorial(_html("/compatibility/maker/")) is None


def test_a_term_page_without_a_landing_page_is_the_plain_listing(
    seeded: SeedReport, maker: Term
) -> None:
    listed("Steel bag").terms.add(maker)
    assert _editorial(_html("/compatibility/maker/")) is None


def test_the_landing_page_follows_the_page_language(seeded: SeedReport, maker: Term) -> None:
    page = term_landing(maker)
    assert _editorial(_html("/it/compatibilita/marca/")) is None  # no IT landing page yet
    copy = _translate(page, "it", body=_intro("Tutto sulla marca."))
    assert copy.url == "/it/compatibilita/marca/"
    editorial = _editorial(_html("/it/compatibilita/marca/"))
    assert "Tutto sulla marca." in editorial.get_text()
    assert "Everything about the maker." in _editorial(_html("/compatibility/maker/")).get_text()


def test_related_articles_link_to_live_pages(seeded: SeedReport, maker: Term) -> None:
    related = [("page", {"page": _about(), "label": "", "text": ""})]
    term_landing(maker, related=related)
    html = _html("/compatibility/maker/")
    section = html.find(class_="term-landing-related")
    assert section is not None
    assert [a["href"] for a in section.find_all("a")] == [_about().url]


def test_a_landing_page_of_a_term_without_term_pages_has_no_url(seeded: SeedReport) -> None:
    colour = TaxonomyFactory.create(code="colour", has_landing=False)
    TaxonomyTranslationFactory.create(
        taxonomy=colour, name="Colour", slug="colour", status=PUBLISHED
    )
    red = TermFactory.create(taxonomy=colour)
    TermTranslationFactory.create(term=red, name="Red", slug="red", status=PUBLISHED)
    with pytest.raises(ValidationError):
        _home().add_child(instance=TermLandingPage(title="Red", slug="red-guide", term=red))


def test_one_landing_page_per_term_and_language(seeded: SeedReport, maker: Term) -> None:
    term_landing(maker)
    with pytest.raises(ValidationError):
        _home().add_child(instance=TermLandingPage(title="Again", slug="again", term=maker))
    # Another language has its own landing page (a translation).
    other = term_landing(maker, parent=_home("it"), slug="guida")
    assert other.locale.language_code == "it"


def test_the_preview_renders_the_editorial_and_the_products(
    seeded: SeedReport, maker: Term, bags: Category
) -> None:
    listed("Steel bag", category=bags).terms.add(maker)
    page = term_landing(maker, publish=False)
    response = page.make_preview_request()
    assert response.status_code == 200
    html: Any = BeautifulSoup(response.content, "html.parser")
    assert "Everything about the maker." in html.get_text()
    assert "Steel bag" in html.get_text()


# --- Vendor landing pages


def test_a_vendor_page_tells_the_story_and_features_the_products(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=acme)
    listed("Acme draft", vendor=acme, status=Product.Status.DRAFT)
    listed("Other cup")
    page = vendor_landing(acme)
    html = _html(page.url)
    assert html.find("h1").get_text(strip=True) == "Our workshop"
    assert "Three generations of craft." in html.get_text()
    featured = html.find("section", class_="vendor-landing-products")
    assert _titles(featured) == ["Acme bag"]
    assert featured.find("a", class_="product-grid-all")["href"] == "/shop/acme/"


def test_a_vendor_page_without_featured_products(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    listed("Acme bag", vendor=acme)
    page = vendor_landing(acme, featured_products=0)
    assert _html(page.url).find("section", class_="vendor-landing-products") is None


def test_the_vendor_page_has_its_own_canonical_and_hreflang(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    page = vendor_landing(acme)
    _translate(page, "it", slug="il-laboratorio")
    html = _html(page.url)
    assert [link["href"] for link in html.find_all("link", rel="canonical")] == [
        "http://testserver/our-workshop/"
    ]
    alternates = {link["hreflang"]: link["href"] for link in html.find_all("link", rel="alternate")}
    assert alternates == {
        "en": "http://testserver/our-workshop/",
        "it": "http://testserver/it/il-laboratorio/",
        "x-default": "http://testserver/our-workshop/",
    }


def test_the_page_of_a_vendor_no_longer_active_is_gone(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    page = vendor_landing(acme)
    Vendor.objects.filter(pk=acme.pk).update(status=Vendor.Status.SUSPENDED)
    assert Client().get("/our-workshop/").status_code == 404
    page.refresh_from_db()
    assert page.vendor.status == Vendor.Status.SUSPENDED
    assert page.get_url() is None


def test_a_vendor_page_needs_an_active_vendor(seeded: SeedReport) -> None:
    applicant = VendorFactory.create()
    with pytest.raises(ValidationError):
        _home().add_child(instance=VendorLandingPage(title="Story", slug="story", vendor=applicant))


def test_one_vendor_page_per_vendor_and_language(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme")
    vendor_landing(acme)
    with pytest.raises(ValidationError):
        _home().add_child(instance=VendorLandingPage(title="Again", slug="again", vendor=acme))


def test_the_storefront_links_to_the_vendor_story(seeded: SeedReport) -> None:
    acme = VendorFactory.create(status=Vendor.Status.ACTIVE, slug="acme", display_name="Acme")
    listed("Acme bag", vendor=acme)
    assert _html("/shop/acme/").find("a", class_="vendor-story-link") is None
    page = vendor_landing(acme)
    link = _html("/shop/acme/").find("a", class_="vendor-story-link")
    assert link["href"] == page.url


# --- Editing


def test_an_editor_adds_landing_pages_with_choosers_under_the_csp(
    client: Client, seeded: SeedReport
) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    home = _home()
    for model in ("termlandingpage", "vendorlandingpage"):
        response = client.get(f"/admin/pages/add/cms/{model}/{home.pk}/")
        assert response.status_code == 200
        nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"])
        assert nonce is not None
        html = response.content.decode()
        assert f'<script nonce="{nonce[1]}">new Chooser(' in html
        assert not re.search(r"<script>(?!</script>)", html)

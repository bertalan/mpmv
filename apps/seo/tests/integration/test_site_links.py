"""OPS.12: the temporary staff "Site links" page `/_links/` (remove before LIVE).

Anonymous visitors are sent to the site sign-in page (`LOGIN_URL`, not the Django admin's),
signed-in users without staff status get 404, staff get the list of every page a person can
open. Every listed link, followed as staff, answers neither 404 nor 500 (the owner's
"verified links"). The storefront header links to the page for everyone.
"""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.shortcuts import resolve_url
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.catalog.models import Brand, Product
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
from apps.cms.tests.factories import StandardPageFactory
from apps.core.indexing import robots_txt
from apps.core.models import TranslationStatus
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorTranslationFactory

pytestmark = pytest.mark.django_db

URL = "/_links/"
PUBLISHED = TranslationStatus.PUBLISHED
MfaLogin = Callable[[Client, Any], Client]


@pytest.fixture
def staff_client(client: Client, superuser: Any, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


@pytest.fixture
def demo() -> dict[str, Any]:
    """Demo-like data: a CMS page, an active shop, a landing term, a category, a brand and a
    published product."""
    page = StandardPageFactory.create(title="Riding season")
    vendor = VendorFactory.create(display_name="Throttle Garage", status=Vendor.Status.ACTIVE)
    VendorTranslationFactory.create(vendor=vendor, language="en", status=PUBLISHED)
    taxonomy = TaxonomyFactory.create(code="material", has_landing=True)
    TaxonomyTranslationFactory.create(
        taxonomy=taxonomy, language="en", slug="material", status=PUBLISHED
    )
    term = TermFactory.create(taxonomy=taxonomy)
    TermTranslationFactory.create(
        term=term, language="en", name="Leather", slug="leather", status=PUBLISHED
    )
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, language="en", name="Jackets", slug="jackets", status=PUBLISHED
    )
    brand = BrandFactory.create(name="Roadstar", status=Brand.Status.APPROVED)
    BrandTranslationFactory.create(brand=brand, language="en", slug="roadstar", status=PUBLISHED)
    product = ProductFactory.create(vendor=vendor, status=Product.Status.PUBLISHED)
    ProductTranslationFactory.create(
        product=product, language="en", title="Touring jacket", slug="touring-jacket"
    )
    product.terms.add(term)
    return {"page": page, "vendor": vendor}


def listed_paths(html: str) -> list[str]:
    main = html.split('<main id="main"', 1)[1].split("</main>", 1)[0]
    return re.findall(r'<a href="([^"]+)"', main)


def test_anonymous_visitors_are_sent_to_the_site_sign_in(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.path == resolve_url(settings.LOGIN_URL) == "/account/login/"
    assert location.query == "next=/_links/"


def test_signed_in_users_without_staff_status_get_404(client: Client) -> None:
    client.force_login(UserFactory.create())
    response = client.get(URL)
    assert response.status_code == 404
    assert b"Site links" not in response.content.split(b"<main", 1)[-1]


def test_staff_see_pages_shops_and_the_manager(staff_client: Client, demo: dict[str, Any]) -> None:
    response = staff_client.get(URL)
    assert response.status_code == 200
    html = response.content.decode()
    paths = listed_paths(html)
    assert "/riding-season/" in paths
    assert "Riding season" in html
    assert "/shop/throttle-garage/" in paths
    assert "Throttle Garage" in html
    assert "/manager/shop/" in paths
    assert "/manager/platform/" in paths
    assert "/material/leather/" in paths
    assert "/product-category/jackets/" in paths
    assert "/brand/roadstar/" in paths
    assert "/api/v1/docs" in paths


def test_the_page_is_never_indexed_nor_stored(staff_client: Client) -> None:
    response = staff_client.get(URL)
    assert "no-store" in response["Cache-Control"]
    assert "noindex" in response["X-Robots-Tag"]
    assert re.search(r'<meta name="robots" content="noindex[^"]*">', response.content.decode())
    assert "Disallow: /_links/\n" in robots_txt(forced=False)


def test_every_listed_link_answers_without_404_or_500(
    staff_client: Client, demo: dict[str, Any]
) -> None:
    paths = listed_paths(staff_client.get(URL).content.decode())
    assert len(paths) > 20
    broken = {}
    for path in paths:
        status = staff_client.get(path).status_code
        if status in {404, 500}:
            broken[path] = status
    assert broken == {}


def test_the_storefront_header_links_to_the_page(client: Client) -> None:
    page = StandardPageFactory.create(title="About us")
    html = client.get(page.get_url()).content.decode()
    header = html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
    assert re.search(r'<nav id="site-nav"[^>]*>.*<a href="/_links/">Site links</a>', header, re.S)

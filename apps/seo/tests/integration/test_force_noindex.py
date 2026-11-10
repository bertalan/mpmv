"""SEO.8 (1/2): `MPMV_FORCE_NOINDEX` makes every response noindex (D-35)."""

import re
from typing import Any

import pytest
from django.apps import apps
from django.test import Client, override_settings
from wagtail.models import Page, Site

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.models import Product
from apps.catalog.tests.factories import (
    CategoryFactory,
    CategoryTranslationFactory,
    ProductFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

NOINDEX = "noindex, nofollow"
ROBOTS_META = re.compile(r'<meta name="robots" content="([^"]*)"')


def metas(response: Any) -> list[str]:
    return ROBOTS_META.findall(response.content.decode())


def category_url() -> str:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=category, language="en", status="published")
    product = ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE),
        status=Product.Status.PUBLISHED,
    )
    product.categories.add(category)
    translation = category.translations.get(language="en")
    return f"/product-category/{translation.slug}/"


def cms_url() -> str:
    standard_page = apps.get_model("cms", "StandardPage")
    home: Page = Site.objects.get(is_default_site=True).root_page
    home.add_child(instance=standard_page(title="About", slug="about-seo8"))
    return "/about-seo8/"


@override_settings(FORCE_NOINDEX=True)
def test_forced_every_public_page_is_noindex_in_meta_and_header(client: Client) -> None:
    for url in (category_url(), cms_url(), "/", "/no-such-page-seo8/"):
        response = client.get(url)
        assert response["X-Robots-Tag"] == NOINDEX, url
        assert metas(response) == [NOINDEX], (url, response.status_code)


@override_settings(FORCE_NOINDEX=True)
def test_forced_header_is_on_non_html_responses(client: Client) -> None:
    for url in ("/healthz", "/robots.txt", "/media/images/missing.webp"):
        assert client.get(url)["X-Robots-Tag"] == NOINDEX, url


@override_settings(FORCE_NOINDEX=True)
def test_forced_signed_in_manager_page_is_noindex(client: Client) -> None:
    staff = UserFactory.create(permissions=["core.platform_owner"])
    verified_login(client, staff)
    response = client.get("/manager/platform/")
    assert response.status_code == 200
    assert response["X-Robots-Tag"] == NOINDEX
    assert metas(response) == [NOINDEX]


@override_settings(FORCE_NOINDEX=True)
def test_forced_robots_txt_disallows_everything(client: Client) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == "User-agent: *\nDisallow: /\n"


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=True)
def test_demo_mode_forces_noindex(client: Client) -> None:
    response = client.get(cms_url())
    assert response["X-Robots-Tag"] == NOINDEX
    assert client.get("/robots.txt").content.decode() == "User-agent: *\nDisallow: /\n"


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_not_forced_listing_keeps_its_rules_and_sends_no_header(client: Client) -> None:
    response = client.get(category_url())
    assert metas(response) == ["index, follow"]
    assert "X-Robots-Tag" not in response


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_not_forced_cms_page_prints_no_robots_meta(client: Client) -> None:
    response = client.get(cms_url())
    assert metas(response) == []
    assert "X-Robots-Tag" not in response


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_not_forced_robots_txt_allows_with_private_disallows(client: Client) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    text = response.content.decode()
    assert "Disallow: /manager/\n" in text
    assert "Disallow: /django-admin/\n" in text
    assert "\nDisallow: /\n" not in text

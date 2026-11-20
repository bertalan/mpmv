"""SEO.8 2/2: per-page SEO fields in the head (D-36): title, description, robots, og:image."""

import datetime as dt
import re
import secrets
from typing import Any

import pytest
from django.test import Client, override_settings
from wagtail.models import Page, Site

from apps.cms.tests.factories import BlogPostPageFactory, StandardPageFactory
from apps.core.tests.factories import MediaImageFactory, PlatformOperatorFactory
from apps.seo.models import SeoSettings

pytestmark = pytest.mark.django_db


def head(response: Any) -> str:
    return str(response.content.decode().split("</head>")[0])


def meta(html: str, key: str, attr: str = "property") -> list[str]:
    return re.findall(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)"', html)


def get(page: Page) -> str:
    page.save_revision().publish()
    return head(Client().get(page.url))


def test_title_and_description_come_from_the_seo_fields() -> None:
    page = StandardPageFactory.create(
        title="About", seo_title="Who we are", search_description="Our story"
    )
    html = get(page)
    assert "<title>Who we are" in html
    assert '<meta name="description" content="Our story">' in html
    assert meta(html, "og:title") == ["Who we are"]
    assert meta(html, "og:description") == ["Our story"]


def test_the_page_title_and_the_summary_are_the_fallbacks() -> None:
    post = BlogPostPageFactory.create(title="News", summary="A short summary")
    html = get(post)
    assert "<title>News" in html
    assert '<meta name="description" content="A short summary">' in html
    assert meta(html, "og:title") == ["News"]


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_a_noindex_page_prints_one_robots_meta() -> None:
    SeoSettings.objects.create(site=Site.objects.get(is_default_site=True), allow_indexing=True)
    html = get(StandardPageFactory.create(noindex=True))
    assert html.count('name="robots"') == 1
    assert 'content="noindex"' in html


@override_settings(FORCE_NOINDEX=True)
def test_the_floor_wins_over_the_page_rule() -> None:
    allowed = get(StandardPageFactory.create(noindex=False))
    own = get(StandardPageFactory.create(noindex=True))
    for html in (allowed, own):
        assert re.findall(r'<meta name="robots" content="([^"]*)"', html) == ["noindex, nofollow"]


def test_og_image_prefers_the_page_image_then_the_site_default_then_the_logo() -> None:
    token = secrets.token_hex(4)
    page_image = MediaImageFactory.create(file__filename=f"page{token}.png")
    site_image = MediaImageFactory.create(file__filename=f"site{token}.png")
    logo = MediaImageFactory.create(file__filename=f"logo{token}.png")
    page = StandardPageFactory.create(seo_image=page_image)
    assert f"page{token}" in meta(get(page), "og:image")[0]
    assert meta(get(page), "twitter:card", "name") == ["summary_large_image"]

    page.seo_image = None
    SeoSettings.objects.create(
        site=Site.objects.get(is_default_site=True), default_image=site_image
    )
    assert f"site{token}" in meta(get(page), "og:image")[0]

    SeoSettings.objects.all().delete()
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), logo=logo
    )
    found = meta(get(page), "og:image")
    assert found
    assert found[0].startswith("http")
    assert f"logo{token}" in found[0]


def test_no_image_anywhere_prints_no_og_image() -> None:
    html = get(StandardPageFactory.create())
    assert meta(html, "og:image") == []
    assert meta(html, "twitter:card", "name") == ["summary"]


def test_the_default_image_is_set_through_the_audited_service() -> None:
    from apps.accounts.tests.factories import UserFactory
    from apps.core.models import AuditLog
    from apps.seo import services

    image = MediaImageFactory.create()
    site = Site.objects.get(is_default_site=True)
    services.set_site_indexing(
        UserFactory.create(superuser=True), site, False, ctx=None, default_image=image
    )
    assert SeoSettings.current(site).default_image_id == image.pk
    row = AuditLog.objects.filter(action=services.SEO_SETTINGS_CONFIGURED).latest("pk")
    assert (row.after or {})["default_image"] == image.pk
    # Not passing it leaves it alone.
    services.set_site_indexing(UserFactory.create(superuser=True), site, False, ctx=None)
    assert SeoSettings.current(site).default_image_id == image.pk

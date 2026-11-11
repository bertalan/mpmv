"""SEO.8 (2/2): the per-site indexing toggle on top of the environment floor (D-35)."""

import re
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from wagtail.models import Page, Site

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import user_context
from apps.core.models import AuditLog
from apps.seo import services
from apps.seo.models import SeoSettings

pytestmark = pytest.mark.django_db

NOINDEX = "noindex, nofollow"
ROBOTS_META = re.compile(r'<meta name="robots" content="([^"]*)"')
SETTINGS_URL = "seo_admin:settings"


def metas(response: Any) -> list[str]:
    return ROBOTS_META.findall(response.content.decode())


def default_site() -> Site:
    site: Site = Site.objects.get(is_default_site=True)
    return site


def cms_url() -> str:
    standard_page = apps.get_model("cms", "StandardPage")
    home: Page = default_site().root_page
    home.add_child(instance=standard_page(title="About", slug="about-seo82"))
    return "/about-seo82/"


def staff() -> Any:
    return UserFactory.create(superuser=True)


def set_indexing(allow: bool) -> None:
    services.set_site_indexing(staff(), default_site(), allow, ctx=None)


# --- Effect: meta, header, robots.txt


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_a_new_site_is_not_indexed_until_the_owner_allows_it(client: Client) -> None:
    assert not SeoSettings.objects.exists()
    response = client.get(cms_url())
    assert response["X-Robots-Tag"] == NOINDEX
    assert metas(response) == [NOINDEX]
    assert client.get("/robots.txt").content.decode() == "User-agent: *\nDisallow: /\n"


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_toggle_off_is_noindex_in_meta_header_and_robots_txt(client: Client) -> None:
    set_indexing(True)
    set_indexing(False)
    response = client.get(cms_url())
    assert response["X-Robots-Tag"] == NOINDEX
    assert metas(response) == [NOINDEX]
    assert client.get("/robots.txt").content.decode() == "User-agent: *\nDisallow: /\n"


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_toggle_on_without_floor_gives_the_normal_rules(client: Client) -> None:
    set_indexing(True)
    response = client.get(cms_url())
    assert "X-Robots-Tag" not in response
    assert metas(response) == []
    robots = client.get("/robots.txt").content.decode()
    assert "\nDisallow: /\n" not in robots
    assert "Disallow: /manager/\n" in robots


@override_settings(FORCE_NOINDEX=True)
def test_the_floor_wins_over_a_stored_allow(client: Client) -> None:
    SeoSettings.objects.create(site=default_site(), allow_indexing=True)
    response = client.get(cms_url())
    assert response["X-Robots-Tag"] == NOINDEX
    assert metas(response) == [NOINDEX]
    assert client.get("/robots.txt").content.decode() == "User-agent: *\nDisallow: /\n"


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=True)
def test_demo_mode_wins_over_a_stored_allow(client: Client) -> None:
    SeoSettings.objects.create(site=default_site(), allow_indexing=True)
    assert client.get(cms_url())["X-Robots-Tag"] == NOINDEX


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_a_toggle_change_shows_on_the_next_request_to_the_same_page(client: Client) -> None:
    # No page is stored in a server-side cache: the verdict is read per request, so a page the
    # proxy re-fetches carries the new rule at once.
    url = cms_url()
    set_indexing(True)
    assert "X-Robots-Tag" not in client.get(url)
    set_indexing(False)
    assert client.get(url)["X-Robots-Tag"] == NOINDEX
    set_indexing(True)
    assert "X-Robots-Tag" not in client.get(url)


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_only_the_site_of_the_request_is_affected(client: Client) -> None:
    other = Site.objects.create(
        hostname="other.example.test", root_page=default_site().root_page, port=80
    )
    SeoSettings.objects.create(site=other, allow_indexing=True)
    # The default site (the request's, host "testserver") has no row: noindex.
    assert client.get(cms_url())["X-Robots-Tag"] == NOINDEX
    assert "X-Robots-Tag" not in client.get("/healthz", HTTP_HOST="other.example.test")


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_listings_follow_the_toggle(client: Client) -> None:
    from apps.catalog.models import Product
    from apps.catalog.tests.factories import (
        CategoryFactory,
        CategoryTranslationFactory,
        ProductFactory,
    )
    from apps.vendors.models import Vendor
    from apps.vendors.tests.factories import VendorFactory

    category = CategoryFactory.create()
    CategoryTranslationFactory.create(category=category, language="en", status="published")
    ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE),
        category=category,
        status=Product.Status.PUBLISHED,
    )
    url = f"/product-category/{category.translations.get(language='en').slug}/"
    assert metas(client.get(url)) == [NOINDEX]
    set_indexing(True)
    assert metas(client.get(url)) == ["index, follow"]


# --- Sitemap line in robots.txt


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_robots_txt_has_no_sitemap_line_while_no_sitemap_route_exists(client: Client) -> None:
    set_indexing(True)
    assert "Sitemap:" not in client.get("/robots.txt").content.decode()


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False, ROOT_URLCONF="apps.seo.tests.urls")
def test_robots_txt_lists_the_sitemap_when_indexing_is_allowed(client: Client) -> None:
    set_indexing(True)
    text = client.get("/robots.txt").content.decode()
    assert text.endswith("Sitemap: http://testserver/sitemap.xml\n")


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False, ROOT_URLCONF="apps.seo.tests.urls")
def test_a_noindex_robots_txt_has_no_sitemap_line(client: Client) -> None:
    text = client.get("/robots.txt").content.decode()
    assert "Sitemap:" not in text


# --- The service


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_set_site_indexing_stores_and_audits() -> None:
    user = staff()
    site = default_site()
    services.set_site_indexing(user, site, True, ctx=user_context(user, ip=None))
    assert SeoSettings.objects.get(site=site).allow_indexing is True
    entry = AuditLog.objects.get(target_model="seo.seosettings")
    assert entry.action == services.SEO_SETTINGS_CONFIGURED
    assert entry.after["allow_indexing"] is True
    services.set_site_indexing(user, site, False, ctx=user_context(user, ip=None))
    assert AuditLog.objects.filter(target_model="seo.seosettings").count() == 2


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_an_unchanged_value_is_not_audited() -> None:
    set_indexing(True)
    before = AuditLog.objects.filter(target_model="seo.seosettings").count()
    set_indexing(True)
    assert AuditLog.objects.filter(target_model="seo.seosettings").count() == before


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_dry_run_changes_nothing() -> None:
    services.set_site_indexing(staff(), default_site(), True, ctx=None, dry_run=True)
    assert not SeoSettings.objects.exists()
    assert not AuditLog.objects.filter(target_model="seo.seosettings").exists()


@pytest.mark.parametrize("override", [{"FORCE_NOINDEX": True}, {"DEMO_MODE": True}])
def test_the_service_refuses_to_allow_indexing_under_the_floor(override: dict[str, bool]) -> None:
    with override_settings(**{"FORCE_NOINDEX": False, "DEMO_MODE": False, **override}):
        with pytest.raises(ValidationError):
            services.set_site_indexing(staff(), default_site(), True, ctx=None)
        # Turning it off under the floor is allowed (it only adds noindex).
        services.set_site_indexing(staff(), default_site(), False, ctx=None)
    assert SeoSettings.objects.get(site=default_site()).allow_indexing is False


def test_the_service_is_staff_only() -> None:
    with pytest.raises(PermissionDenied):
        services.set_site_indexing(UserFactory.create(), default_site(), False, ctx=None)


# --- The panel (Wagtail admin, Settings menu)


def _refused(response: Any) -> bool:
    if response.status_code == 403:
        return True
    return bool(
        response.status_code == 302 and response["Location"] == reverse("wagtailadmin_home")
    )


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_staff_open_the_panel_and_a_get_writes_nothing(client: Client) -> None:
    verified_login(client, staff())
    response = client.get(reverse(SETTINGS_URL))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'name="allow_indexing"' in html
    assert "disabled" not in html.split('name="allow_indexing"')[1].split(">")[0]
    assert not SeoSettings.objects.exists()


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_staff_allow_indexing_through_the_panel(client: Client) -> None:
    verified_login(client, staff())
    site = default_site()
    response = client.post(reverse(SETTINGS_URL), {"site": site.pk, "allow_indexing": "on"})
    assert response.status_code == 302
    assert SeoSettings.objects.get(site=site).allow_indexing is True
    assert AuditLog.objects.filter(target_model="seo.seosettings").count() == 1


@override_settings(FORCE_NOINDEX=True)
def test_the_panel_is_read_only_under_the_floor(client: Client) -> None:
    verified_login(client, staff())
    site = default_site()
    response = client.get(reverse(SETTINGS_URL))
    html = response.content.decode()
    assert "MPMV_FORCE_NOINDEX" in html
    assert "disabled" in html.split('name="allow_indexing"')[1].split(">")[0]
    post = client.post(reverse(SETTINGS_URL), {"site": site.pk, "allow_indexing": "on"})
    assert post.status_code in (200, 302)
    assert not SeoSettings.objects.filter(site=site, allow_indexing=True).exists()


@override_settings(FORCE_NOINDEX=False, DEMO_MODE=False)
def test_a_non_staff_editor_cannot_use_the_panel(client: Client) -> None:
    from django.contrib.auth.models import Group, Permission

    user = UserFactory.create()
    group = Group.objects.create(name="SEO editors")
    group.permissions.add(Permission.objects.get(codename="access_admin"))
    user.groups.add(group)
    verified_login(client, user)
    assert _refused(client.get(reverse(SETTINGS_URL)))
    site = default_site()
    assert _refused(client.post(reverse(SETTINGS_URL), {"site": site.pk, "allow_indexing": "on"}))
    assert not SeoSettings.objects.exists()


def test_the_panel_is_in_the_settings_menu(client: Client) -> None:
    verified_login(client, staff())
    response = client.get(reverse("wagtailadmin_home"))
    assert reverse(SETTINGS_URL) in response.content.decode()

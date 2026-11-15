"""CORE.30 done-when: the platform map lists every Manager page for platform staff only."""

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.manager import site_map
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

from .conftest import LOGIN, PLATFORM

pytestmark = pytest.mark.django_db

MAP = "/manager/platform/map/"


def _manager_names(patterns: list[URLPattern | URLResolver], prefix: str = "") -> set[str]:
    names: set[str] = set()
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            ns = f"{prefix}{pattern.namespace}:" if pattern.namespace else prefix
            names |= _manager_names(pattern.url_patterns, ns)
        elif pattern.name and prefix.startswith("manager:"):
            names.add(f"{prefix}{pattern.name}")
    return names


def test_every_manager_url_is_on_the_map_or_named_as_not_a_page() -> None:
    names = _manager_names(get_resolver().url_patterns)
    assert "manager:platform:map" in names
    listed = site_map.mapped_names()
    assert names - listed - site_map.NOT_LISTED == set()
    assert listed <= names
    assert names >= site_map.NOT_LISTED
    assert not listed & site_map.NOT_LISTED


def test_staff_see_a_link_for_every_listed_manager_page(
    client: Client, platform_staff: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    verified_login(client, platform_staff)
    response = client.get(MAP)
    assert response.status_code == 200
    page = response.text
    for name in site_map.mapped_names():
        if name.startswith("manager:platform:"):
            assert f'href="{reverse(name)}"' in page, name
        else:  # shop pages: plain text for staff, once per vendor
            assert page.count(f"<code>{reverse(name)}</code>") == 2, name
    assert page.index("Shop One") < page.index("Shop Two")
    assert f'href="{reverse("home")}"' in page
    assert f'href="{reverse("orders:cart")}"' in page
    assert f'href="{reverse("consent_preferences")}"' in page
    assert "/django-admin/" not in page


def test_a_vendor_member_gets_the_platform_space_refusal(client: Client, owner: User) -> None:
    verified_login(client, owner)
    assert client.get(MAP).status_code == 403
    assert client.get(PLATFORM).status_code == 403


def test_an_anonymous_visitor_is_sent_to_login(client: Client) -> None:
    response = client.get(MAP)
    assert response.status_code == 302
    assert response["Location"].startswith(LOGIN)


def test_the_dashboard_links_to_the_map(client: Client, platform_staff: User) -> None:
    VendorFactory.create()
    verified_login(client, platform_staff)
    assert f'href="{MAP}"' in client.get(PLATFORM).text


ACCOUNT = "/account/orders/"


def test_a_vendor_owner_reaches_the_manager_from_the_site(client: Client, owner: User) -> None:
    verified_login(client, owner)
    page = client.get(ACCOUNT).text
    assert page.count('href="/manager/shop/"') == 2  # header account menu and account area
    assert "My shop" in page


def test_platform_staff_reach_the_manager_from_the_site(
    client: Client, platform_staff: User
) -> None:
    verified_login(client, platform_staff)
    page = client.get(ACCOUNT).text
    assert page.count(f'href="{PLATFORM}"') == 2
    assert ">Manager</a>" in page


def test_a_plain_customer_gets_no_manager_entry(client: Client, customer: User) -> None:
    client.force_login(customer)
    page = client.get(ACCOUNT).text
    assert "/manager/" not in page
    assert "My shop" not in page


def test_an_anonymous_visitor_gets_no_manager_entry(client: Client) -> None:
    response = client.get("/cookie-settings/")
    assert response.status_code == 200
    assert "/manager/" not in response.text

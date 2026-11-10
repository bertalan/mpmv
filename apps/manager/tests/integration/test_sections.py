"""CORE.17 done-when: the menu shows only the sections the actor may open, each view checks the
same permission, and a section hidden from the menu still answers 404 when its URL is typed."""

from collections.abc import Iterator

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.manager.navigation import registry
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from . import views
from .conftest import PLATFORM, SHOP

pytestmark = pytest.mark.django_db

OWNER_ONLY = "/manager/shop/test-owner-only/"


@pytest.fixture(autouse=True)
def _test_sections(settings: object) -> Iterator[None]:
    settings.ROOT_URLCONF = "apps.manager.tests.integration.urls"  # type: ignore[attr-defined]
    with registry.extended(*views.TEST_SECTIONS):
        yield


@pytest.fixture
def staff_member(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="staff").user


def test_the_menu_lists_the_sections_the_owner_may_open(client: Client, owner: User) -> None:
    verified_login(client, owner)
    page = client.get(SHOP).text
    assert 'href="/manager/shop/"' in page
    assert f'href="{OWNER_ONLY}"' in page
    assert client.get(OWNER_ONLY).status_code == 200


def test_a_section_hidden_from_the_menu_still_answers_404_when_typed(
    client: Client, staff_member: User
) -> None:
    verified_login(client, staff_member)
    page = client.get(SHOP).text
    assert 'href="/manager/shop/"' in page
    assert OWNER_ONLY not in page
    assert client.get(OWNER_ONLY).status_code == 404
    assert client.post(OWNER_ONLY).status_code == 404


def test_the_current_section_is_marked_in_the_menu(client: Client, owner: User) -> None:
    verified_login(client, owner)
    page = client.get(OWNER_ONLY).text
    assert f'href="{OWNER_ONLY}" aria-current="page"' in page


@pytest.mark.parametrize(
    "path",
    [
        "/manager/shop/test-wrong-space/",
        "/manager/shop/test-no-section/",
        "/manager/shop/test-unknown-section/",
    ],
)
def test_a_view_without_a_section_of_its_space_is_404(
    client: Client, owner: User, path: str
) -> None:
    verified_login(client, owner)
    assert client.get(path).status_code == 404


def test_the_platform_menu_lists_its_dashboard(client: Client, platform_staff: User) -> None:
    verified_login(client, platform_staff)
    page = client.get(PLATFORM).text
    assert 'href="/manager/platform/"' in page
    assert "/manager/shop/" not in page

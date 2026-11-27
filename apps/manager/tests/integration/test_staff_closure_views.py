"""VEN.20 slice 3b: platform staff manage the closures of any shop (owner decision 2026-11-26)."""

import datetime as dt
import uuid
from typing import Any

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorClosure
from apps.vendors.tests.factories import VendorClosureFactory

from .conftest import PLATFORM

pytestmark = pytest.mark.django_db

PICKER = f"{PLATFORM}shop-closures/"
START = dt.date.today() + dt.timedelta(days=60)


def _data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "start_date": str(START),
        "end_date": str(START + dt.timedelta(days=10)),
        "notice_days": "14",
    }
    data.update(changes)
    return data


def _page(vendor: Vendor) -> str:
    return f"{PICKER}{vendor.public_id}/"


def _staff(role: str, **client_args: Any) -> Client:
    client = Client(**client_args)
    verified_login(client, UserFactory.create(platform_role=role))
    return client


@pytest.mark.parametrize("role", ["support", "owner"])
def test_staff_with_the_row_manage_any_shops_closures_and_it_is_audited(
    role: str, vendor: Vendor
) -> None:
    client = _staff(role)
    assert vendor.display_name in client.get(PICKER).content.decode()
    assert client.get(_page(vendor)).status_code == 200
    assert client.post(_page(vendor), _data(repeats_yearly="on")).status_code == 302
    closure = VendorClosure.objects.get(vendor=vendor)
    entry = AuditLog.objects.get(action="vendors.closure.added")
    assert entry.source == AuditLog.Source.ADMIN
    assert entry.actor_label

    page = f"{_page(vendor)}{closure.public_id}/"
    assert client.get(page).status_code == 200
    assert client.post(page, _data(notice_days="3")).status_code == 302
    closure.refresh_from_db()
    assert closure.notice_days == 3
    assert AuditLog.objects.filter(action="vendors.closure.changed").exists()

    assert client.post(f"{page}remove/").status_code == 302
    assert not VendorClosure.objects.exists()
    assert AuditLog.objects.filter(action="vendors.closure.removed").exists()


def test_the_list_links_stay_in_the_platform_space(vendor: Vendor) -> None:
    closure = VendorClosureFactory.create(vendor=vendor)
    text = _staff("support").get(_page(vendor)).content.decode()
    assert f"{_page(vendor)}{closure.public_id}/" in text
    assert "/manager/shop/" not in text.split("<main", 1)[-1]


@pytest.mark.parametrize("role", ["catalog", "config", "finance"])
def test_staff_without_the_row_get_404_and_nothing_changes(role: str, vendor: Vendor) -> None:
    closure = VendorClosureFactory.create(vendor=vendor)
    client = _staff(role)
    page = f"{_page(vendor)}{closure.public_id}/"
    for url in (PICKER, _page(vendor), page, f"{page}remove/"):
        assert client.get(url).status_code == 404
    assert client.post(_page(vendor), _data()).status_code == 404
    assert client.post(page, _data(notice_days="1")).status_code == 404
    assert client.post(f"{page}remove/").status_code == 404
    closure.refresh_from_db()
    assert closure.notice_days == 14
    assert VendorClosure.objects.count() == 1
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_a_vendor_member_gets_the_403_page_on_get_and_post(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    closure = VendorClosureFactory.create(vendor=other_vendor)
    verified_login(client, owner)
    page = f"{_page(other_vendor)}{closure.public_id}/"
    for url in (PICKER, _page(other_vendor), page, f"{page}remove/"):
        assert client.get(url).status_code == 403
        assert client.post(url, _data()).status_code == 403
    assert VendorClosure.objects.count() == 1
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_an_unknown_shop_or_a_closure_of_another_shop_is_a_404(
    vendor: Vendor, other_vendor: Vendor
) -> None:
    client = _staff("support")
    ghost = f"{PICKER}{uuid.uuid4()}/"
    assert client.get(ghost).status_code == 404
    assert client.post(ghost, _data()).status_code == 404
    theirs = VendorClosureFactory.create(vendor=other_vendor)
    forged = f"{_page(vendor)}{theirs.public_id}/"  # the right closure under the wrong shop
    for url in (forged, f"{forged}remove/"):
        assert client.get(url).status_code == 404
        assert client.post(url, _data(notice_days="1")).status_code == 404
    theirs.refresh_from_db()
    assert theirs.notice_days == 14
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_the_list_shows_only_the_chosen_shops_closures(
    vendor: Vendor, other_vendor: Vendor
) -> None:
    mine = VendorClosureFactory.create(vendor=vendor)
    theirs = VendorClosureFactory.create(vendor=other_vendor)
    text = _staff("support").get(_page(vendor)).content.decode()
    assert str(mine.public_id) in text
    assert str(theirs.public_id) not in text


def test_an_ended_range_cannot_be_changed_or_removed_even_by_staff(vendor: Vendor) -> None:
    past = dt.date.today() - dt.timedelta(days=20)
    closure = VendorClosureFactory.create(
        vendor=vendor, start_date=past - dt.timedelta(days=3), end_date=past
    )
    client = _staff("owner")
    page = f"{_page(vendor)}{closure.public_id}/"
    assert client.post(page, _data()).status_code == 200
    client.post(f"{page}remove/")
    closure.refresh_from_db()
    assert closure.start_date != START
    assert VendorClosure.objects.filter(pk=closure.pk).exists()
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_the_same_validation_as_the_shop_page_applies(vendor: Vendor) -> None:
    client = _staff("support")
    backwards = _data(end_date=str(START - dt.timedelta(days=1)))
    assert client.post(_page(vendor), backwards).status_code == 200
    leap = _data(start_date="2028-02-29", end_date="2028-03-05", repeats_yearly="on")
    assert client.post(_page(vendor), leap).status_code == 200
    assert not VendorClosure.objects.exists()


def test_a_post_needs_the_csrf_token(vendor: Vendor) -> None:
    client = _staff("support", enforce_csrf_checks=True)
    assert client.post(_page(vendor), _data()).status_code == 403
    assert not VendorClosure.objects.exists()


def test_the_picker_searches_by_name_and_the_menu_lists_the_section(
    vendor: Vendor, other_vendor: Vendor
) -> None:
    client = _staff("support")
    text = client.get(PICKER, {"q": "Shop Two"}).content.decode()
    assert str(other_vendor.public_id) in text
    assert str(vendor.public_id) not in text
    assert PICKER in client.get(PLATFORM).content.decode()

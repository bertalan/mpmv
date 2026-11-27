"""VEN.20 slice 2: the Manager shop page for closures (owner and staff, tenant scoped)."""

import datetime as dt
from datetime import UTC, datetime
from typing import Any

import pytest
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors.models import Vendor, VendorClosure
from apps.vendors.tests.factories import VendorClosureFactory, VendorMemberFactory

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db

URL = f"{SHOP}settings/closures/"
START = dt.date.today() + dt.timedelta(days=60)


def _data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "start_date": str(START),
        "end_date": str(START + dt.timedelta(days=10)),
        "notice_days": "14",
    }
    data.update(changes)
    return data


def _login(client: Client, user: User, vendor: Vendor, **kwargs: Any) -> None:
    verified_login(client, user, **kwargs)
    assert client.post(PICKER, {"vendor": str(vendor.public_id)}).status_code == 302


def _edit(closure: VendorClosure) -> str:
    return f"{URL}{closure.public_id}/"


def test_the_owner_adds_a_closure_which_takes_effect_at_once(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _login(client, owner, vendor)
    assert client.get(URL).status_code == 200
    response = client.post(URL, _data(repeats_yearly="on"))
    assert response.status_code == 302
    closure = VendorClosure.objects.get(vendor=vendor)
    assert (closure.start_date, closure.repeats_yearly) == (START, True)
    entry = AuditLog.objects.get(action="vendors.closure.added")
    assert entry.source == AuditLog.Source.VENDOR
    assert str(closure.public_id) in client.get(URL).content.decode()


def test_a_backwards_range_shows_the_translated_message_and_stores_nothing(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _login(client, owner, vendor)
    response = client.post(URL, _data(end_date=str(START - dt.timedelta(days=1))))
    assert response.status_code == 200
    assert b"The end date cannot be before the start date." in response.content
    assert not VendorClosure.objects.exists()


def test_a_yearly_range_on_29_february_is_refused(
    client: Client, owner: User, vendor: Vendor
) -> None:
    _login(client, owner, vendor)
    response = client.post(
        URL, _data(start_date="2028-02-29", end_date="2028-03-05", repeats_yearly="on")
    )
    assert response.status_code == 200
    assert b"29 February" in response.content
    assert not VendorClosure.objects.exists()


def test_the_owner_edits_and_removes_a_closure(client: Client, owner: User, vendor: Vendor) -> None:
    closure = VendorClosureFactory.create(vendor=vendor)
    _login(client, owner, vendor)
    assert client.get(_edit(closure)).status_code == 200
    assert client.post(_edit(closure), _data(notice_days="3")).status_code == 302
    closure.refresh_from_db()
    assert (closure.start_date, closure.notice_days) == (START, 3)
    assert AuditLog.objects.filter(action="vendors.closure.changed").count() == 1
    assert client.get(f"{_edit(closure)}remove/").status_code == 200
    assert client.post(f"{_edit(closure)}remove/").status_code == 302
    assert not VendorClosure.objects.exists()


def test_an_ended_range_is_history_and_cannot_be_changed_or_removed(
    client: Client, owner: User, vendor: Vendor
) -> None:
    past = dt.date.today() - dt.timedelta(days=20)
    closure = VendorClosureFactory.create(
        vendor=vendor, start_date=past - dt.timedelta(days=3), end_date=past
    )
    _login(client, owner, vendor)
    changed = client.post(_edit(closure), _data())
    assert changed.status_code == 200
    closure.refresh_from_db()
    assert closure.start_date != START
    client.post(f"{_edit(closure)}remove/")
    assert VendorClosure.objects.filter(pk=closure.pk).exists()
    assert str(closure.public_id) not in client.get(URL).content.decode()  # no edit link


def test_another_shops_closure_is_a_404_to_edit_and_to_remove(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    theirs = VendorClosureFactory.create(vendor=other_vendor)
    _login(client, owner, vendor)
    assert client.get(_edit(theirs)).status_code == 404
    assert client.post(_edit(theirs), _data(notice_days="1")).status_code == 404
    assert client.get(f"{_edit(theirs)}remove/").status_code == 404
    assert client.post(f"{_edit(theirs)}remove/").status_code == 404
    theirs.refresh_from_db()
    assert theirs.notice_days == 14
    assert str(theirs.public_id) not in client.get(URL).content.decode()
    assert not AuditLog.objects.filter(action__startswith="vendors.closure.").exists()


def test_a_shop_staff_member_manages_closures_and_the_audit_names_them(
    client: Client, vendor: Vendor
) -> None:
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    _login(client, member, vendor)
    assert client.get(URL).status_code == 200
    assert client.post(URL, _data()).status_code == 302
    closure = VendorClosure.objects.get(vendor=vendor)
    assert client.post(_edit(closure), _data(notice_days="2")).status_code == 302
    entry = AuditLog.objects.get(action="vendors.closure.changed")
    assert entry.actor_id == member.pk
    assert client.post(f"{_edit(closure)}remove/").status_code == 302
    assert not VendorClosure.objects.exists()


def test_a_staff_member_of_another_shop_gets_404_on_this_shops_closure(
    client: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    theirs = VendorClosureFactory.create(vendor=other_vendor)
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    _login(client, member, vendor)
    assert client.get(_edit(theirs)).status_code == 404
    assert client.post(f"{_edit(theirs)}remove/").status_code == 404
    assert VendorClosure.objects.filter(pk=theirs.pk).exists()


def test_a_user_with_no_membership_is_refused(client: Client, customer: User) -> None:
    verified_login(client, customer)
    assert client.get(URL).status_code == 403
    assert not VendorClosure.objects.exists()


def test_the_page_shows_the_notice_buyers_see_now(
    client: Client, owner: User, vendor: Vendor
) -> None:
    VendorClosureFactory.create(
        vendor=vendor,
        start_date=dt.date.today() + dt.timedelta(days=5),
        end_date=dt.date.today() + dt.timedelta(days=9),
    )
    _login(client, owner, vendor)
    assert b"closure-notice" in client.get(URL).content


def test_a_closure_write_needs_the_session_level(
    client: Client, owner: User, vendor: Vendor
) -> None:
    """`content_edit` is an everyday action by default; once the operator makes it sensitive, a
    session with no passkey window is refused on add, edit and remove, and nothing changes."""
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        session_policy={"actions": {"sensitive": ["content_edit"]}},
    )
    mine = VendorClosureFactory.create(vendor=vendor, notice_days=14)
    _login(client, owner, vendor, level=levels.NO_WINDOW)
    assert client.post(URL, _data()).status_code == 403
    assert client.post(_edit(mine), _data(notice_days="30")).status_code == 403
    assert client.post(f"{_edit(mine)}remove/").status_code == 403
    assert VendorClosure.objects.filter(vendor=vendor).count() == 1
    mine.refresh_from_db()
    assert mine.notice_days == 14

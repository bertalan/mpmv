"""ORD.30: the platform list of packages staff can still act on.

`/manager/platform/orders/` keeps the search by order number and lists only the packages on which
an ORD.22 action is still possible (paid, before shipping), oldest first, with filters, pages and
the open amendment. The list and the ORD.22 service share ONE predicate
(`amendments.staff_actionable`). A Catalog or Finance member gets a 404 (the Manager convention for
a page outside one's role, as the order page does; the spec row says 403: see the PR).
"""

import datetime as dt
import itertools
import re
from collections import deque
from collections.abc import Mapping

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from django.utils.translation import gettext

from apps.accounts import levels
from apps.accounts.tests.factories import PasskeyDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.compliance.models import TerminationNotice
from apps.compliance.tests.factories import TerminationNoticeFactory, WithdrawalStatementFactory
from apps.orders import amendments
from apps.orders.engine import ORDER_STATES, ORDER_TRANSITIONS, VENDOR_ORDER_TRANSITIONS
from apps.orders.models import Order, OrderAmendment, VendorOrder, VendorOrderState
from apps.orders.services import OrdersFrozen
from apps.orders.tests.factories import (
    OrderAmendmentFactory,
    OrderFactory,
    OrderLineFactory,
    VendorOrderFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

PLATFORM = "/manager/platform/orders/"
PACKAGE_STATES = [state.value for state in VendorOrderState]
_serial = itertools.count(1)


def staff_login(client: Client, role: str = "support") -> None:
    user = UserFactory.create(platform_role=role)
    PasskeyDeviceFactory.create(user=user)
    verified_login(client, user, level=levels.NO_WINDOW)


def _path(table: Mapping[str, frozenset[str]], start: str, goal: str) -> list[str]:
    """The shortest run of legal transitions from `start` to `goal` (the database refuses a
    state change the machine has no arrow for, so a test reaches a state the way an order does)."""
    seen: dict[str, list[str]] = {start: []}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for nxt in sorted(table.get(here, ())):
            if nxt not in seen:
                seen[nxt] = [*seen[here], nxt]
                queue.append(nxt)
    return seen[goal]


def package(
    order_state: str = "paid",
    state: str = "pending",
    *,
    vendor: object = None,
    age_days: int = 0,
    gross: str = "12.20",
    email: str | None = None,
) -> VendorOrder:
    """A package in the given order and package states (set past the state machine on purpose:
    the predicate must hold for every combination, reachable or not)."""
    number = f"2998-{next(_serial):06d}"
    extra = {"email": email} if email else {}
    order = OrderFactory.create(
        number=number,
        legacy_wp_order_id=900000 + next(_serial),  # keeps `created_at` (the database sets it)
        created_at=timezone.now() - dt.timedelta(days=age_days),
        goods_net=gross,
        grand_total=gross,
        **extra,
    )
    kwargs = {"vendor": vendor} if vendor else {}
    sub = VendorOrderFactory.create(order=order, goods_net=gross, gross_total=gross, **kwargs)
    OrderLineFactory.create(
        vendor_order=sub,
        unit_price_net=gross,
        net_total=gross,
        tax_rate=0,
        tax_amount=0,
    )
    for step in _path(ORDER_TRANSITIONS, "checkout", order_state):
        Order.objects.filter(pk=order.pk).update(state=step)
    for step in _path(VENDOR_ORDER_TRANSITIONS, "pending", state):
        VendorOrder.objects.filter(pk=sub.pk).update(state=step)
    sub.refresh_from_db()
    return sub


def listed(client: Client, query: str = "") -> str:
    response = client.get(f"{PLATFORM}{query}")
    assert response.status_code == 200
    return str(response.text)


def numbers(text: str) -> list[str]:
    return re.findall(r"2998-\d{6}", text)


@pytest.fixture
def support(client: Client) -> Client:
    staff_login(client)
    return client


# --- the predicate ---------------------------------------------------------------------------


def _freeze(vendor: Vendor) -> None:
    TerminationNoticeFactory.create(
        vendor=vendor,
        status=TerminationNotice.Status.APPLIED,
        frozen_at=timezone.now(),
        freeze_reviewed_at=None,
    )


def _withdraw(sub: VendorOrder) -> None:
    WithdrawalStatementFactory.create(vendor_order=sub, order=sub.order)


def _service_says(sub: VendorOrder) -> str | None:
    """What `_lock_amendable` (the ORD.22 actions) answers: None when allowed, else the refusal."""
    try:
        amendments._lock_amendable(sub)
    except OrdersFrozen:
        return "frozen"
    except amendments.AmendmentRefused as exc:
        return "withdrawn" if "withdrawn" in str(exc) else "state"
    return None


@pytest.mark.parametrize("block", [None, "vendor_frozen", "withdrawal_registered"])
@pytest.mark.parametrize("order_state", sorted(ORDER_STATES))
@pytest.mark.parametrize("state", PACKAGE_STATES)
def test_the_list_the_predicate_and_the_service_agree_on_every_state(
    order_state: str, state: str, block: str | None
) -> None:
    sub = package(order_state, state)
    if block == "vendor_frozen":
        _freeze(sub.vendor)
    if block == "withdrawal_registered":
        _withdraw(sub)
    state_ok = order_state not in {"checkout", "quote_flow", "awaiting_payment", "cancelled"} and (
        state in {"pending", "accepted"}
    )
    expected_reason = (
        "vendor_frozen"
        if block == "vendor_frozen"
        else ("withdrawal_registered" if block and state_ok else None)
    )
    expected = state_ok and expected_reason is None
    verdict = amendments.staff_actionable(sub)
    assert verdict == amendments.StaffActionability(expected, expected_reason)
    assert amendments.staff_actionability([sub])[sub.pk] == verdict
    in_list = (
        amendments.staff_actionable_packages(VendorOrder.objects.all()).filter(pk=sub.pk).exists()
    )
    assert in_list is state_ok
    said = _service_says(sub)
    assert (said is None) is expected, (
        f"order {order_state} / package {state} / {block}: the ORD.22 service said {said}, "
        f"the predicate {verdict}"
    )
    if expected_reason:
        assert (
            said
            == {"vendor_frozen": "frozen", "withdrawal_registered": "withdrawn"}[expected_reason]
        )


def test_a_blocked_package_stays_listed_with_its_reason(support: Client) -> None:
    frozen, withdrawn_, plain = package(), package(), package()
    _freeze(frozen.vendor)
    _withdraw(withdrawn_)
    text = listed(support)
    for sub, label in ((frozen, "Shop frozen"), (withdrawn_, "Withdrawal registered")):
        row = re.search(rf"{sub.order.number}.*?</tr>", text, re.S)
        assert row, f"{sub.order.number} must stay listed"
        assert f"data-blocked>{label}<" in row.group(0)
    row = re.search(rf"{plain.order.number}.*?</tr>", text, re.S)
    assert row
    assert "data-blocked" not in row.group(0)


@pytest.mark.parametrize("language", ["it", "de", "fr", "es"])
def test_the_block_badges_are_translated(language: str) -> None:
    with translation.override(language):
        for label in ("Shop frozen", "Withdrawal registered", "Changes are disabled."):
            assert gettext(label) != label


# --- the list --------------------------------------------------------------------------------


def test_a_paid_unshipped_package_is_listed_and_the_others_are_not(support: Client) -> None:
    keep = package("paid", "pending")
    also = package("in_fulfillment", "accepted")
    hidden = [
        package("paid", "shipped"),
        package("fulfilled", "delivered"),
        package("paid", "cancelled"),
        package("closed", "completed"),
        package("refunded", "refunded"),
        package("awaiting_payment", "pending"),
        package("cancelled", "pending"),
    ]
    found = numbers(listed(support))
    assert keep.order.number in found
    assert also.order.number in found
    for sub in hidden:
        assert sub.order.number not in found, f"{sub.order.number} must not be listed"


def test_oldest_first_with_the_columns_and_a_link_to_the_order_page(support: Client) -> None:
    new = package(age_days=1, gross="30.50")
    old = package(age_days=9)
    text = listed(support)
    assert numbers(text) == [old.order.number, new.order.number]
    assert f'href="{PLATFORM}{old.public_id}/"' in text
    assert new.vendor.display_name in text
    assert "30.50" in text
    assert "Pending" in text


def test_an_open_amendment_is_shown_on_its_row(support: Client) -> None:
    sub = package()
    OrderAmendmentFactory.create(vendor_order=sub, from_version__vendor_order=sub)
    plain = package()
    text = listed(support)
    row = re.search(rf"{sub.order.number}.*?</tr>", text, re.S)
    assert row
    assert "Draft" in row.group(0)
    other = re.search(rf"{plain.order.number}.*?</tr>", text, re.S)
    assert other
    assert "Draft" not in other.group(0)
    assert OrderAmendment.objects.filter(vendor_order=sub).count() == 1


def test_filters_by_shop_state_and_placed_date_narrow_the_list(support: Client) -> None:
    shop_a, shop_b = VendorFactory.create(), VendorFactory.create()
    a_old = package(vendor=shop_a, state="pending", age_days=20)
    a_new = package(vendor=shop_a, state="accepted", age_days=2)
    b_new = package(vendor=shop_b, state="pending", age_days=2)
    assert set(numbers(listed(support, f"?shop={shop_a.public_id}"))) == {
        a_old.order.number,
        a_new.order.number,
    }
    assert set(numbers(listed(support, "?state=accepted"))) == {a_new.order.number}
    since = (timezone.now() - dt.timedelta(days=5)).date().isoformat()
    assert set(numbers(listed(support, f"?placed_from={since}"))) == {
        a_new.order.number,
        b_new.order.number,
    }
    until = (timezone.now() - dt.timedelta(days=10)).date().isoformat()
    assert set(numbers(listed(support, f"?placed_to={until}"))) == {a_old.order.number}
    # A malformed value narrows nothing and breaks nothing.
    assert len(numbers(listed(support, "?placed_from=soon&state=nope&shop=x"))) == 3


def test_the_list_is_paginated(support: Client) -> None:
    for day in range(30):
        package(age_days=100 - day)
    first = listed(support)
    assert len(numbers(first)) == 25
    second = listed(support, "?page=2")
    assert len(numbers(second)) == 5
    assert "page=2" in first


def test_the_search_by_number_still_reaches_a_shipped_package(support: Client) -> None:
    shipped = package("paid", "shipped")
    assert shipped.order.number not in numbers(listed(support))
    found = listed(support, f"?number={shipped.order.number}")
    assert f"{PLATFORM}{shipped.public_id}/" in found


def test_the_query_count_does_not_grow_with_the_rows(support: Client) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert support.get(PLATFORM).status_code == 200
        return len(queries)

    package()
    listed(support)  # warm the caches
    few = count()
    for index in range(6):
        sub = package(vendor=VendorFactory.create())
        OrderAmendmentFactory.create(vendor_order=sub, from_version__vendor_order=sub)
        if index % 2:
            _freeze(sub.vendor)
        else:
            _withdraw(sub)
    assert count() == few


def test_no_buyer_contact_data_is_shown(support: Client) -> None:
    package(email="secret.buyer@buyer.test")
    text = listed(support)
    assert "secret.buyer" not in text
    assert "buyer.test" not in text
    assert "1 Test Street" not in text


# --- who may see it --------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["catalog", "finance"])
def test_a_catalog_or_finance_member_is_refused(role: str) -> None:
    package()
    other = Client()
    staff_login(other, role)
    assert other.get(PLATFORM).status_code == 404
    assert other.get(f"{PLATFORM}?number=2998-000001").status_code == 404

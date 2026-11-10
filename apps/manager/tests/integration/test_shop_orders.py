"""ORD.6: the vendor fulfils its own orders from the shop space of the Manager.

List, detail, and the POSTs accept / ship / deliver. Tenant isolation (list, detail, every POST,
forged ids), tracking validation, illegal transitions, audit rows, no buyer e-mail shown.
"""

import uuid

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.orders.models import VendorOrder
from apps.orders.tests.factories import paid_vendor_order
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory
from tests.tenancy import CrossTenant

from .conftest import SHOP

pytestmark = pytest.mark.django_db

ORDERS = f"{SHOP}orders/"


def url(sub: VendorOrder, action: str = "") -> str:
    return f"{ORDERS}{sub.public_id}/" + (f"{action}/" if action else "")


@pytest.fixture
def sub(vendor: Vendor) -> VendorOrder:
    return paid_vendor_order(vendor, order__customer_name="Mario Rossi")


def test_the_list_shows_own_orders_and_a_menu_entry(
    client: Client, owner: User, sub: VendorOrder
) -> None:
    verified_login(client, owner)
    response = client.get(ORDERS)
    assert response.status_code == 200
    assert sub.order.number in response.text
    assert f'href="{ORDERS}"' in client.get(SHOP).text


def test_staff_may_fulfil_too(client: Client, vendor: Vendor, sub: VendorOrder) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    assert client.get(ORDERS).status_code == 200
    assert client.post(url(sub, "accept")).status_code == 302
    assert VendorOrder.objects.get(pk=sub.pk).state == "accepted"


def test_the_detail_shows_what_shipping_needs_but_not_the_buyer_email(
    client: Client,
    owner: User,
    sub: VendorOrder,
) -> None:
    verified_login(client, owner)
    response = client.get(url(sub))
    assert response.status_code == 200
    assert "Mario Rossi" in response.text
    assert "1 Test Street" in response.text
    assert sub.order.email not in response.text


def test_accept_ship_deliver_through_the_page_with_audit_rows(
    client: Client,
    owner: User,
    sub: VendorOrder,
) -> None:
    verified_login(client, owner)
    assert client.post(url(sub, "accept")).status_code == 302
    response = client.post(url(sub, "ship"), {"tracking_number": "TRK-12345", "carrier": "Poste"})
    assert response.status_code == 302
    assert client.post(url(sub, "deliver")).status_code == 302
    sub.refresh_from_db()
    assert (sub.state, sub.tracking_number, sub.carrier) == ("delivered", "TRK-12345", "Poste")
    rows = AuditLog.objects.filter(action="orders.transition", target_model="orders.vendororder")
    assert rows.count() == 3
    assert {r.actor_id for r in rows} == {owner.pk}
    assert {r.source for r in rows} == {AuditLog.Source.VENDOR}


def test_a_bad_tracking_number_redisplays_the_form_and_changes_nothing(
    client: Client,
    owner: User,
    sub: VendorOrder,
) -> None:
    verified_login(client, owner)
    client.post(url(sub, "accept"))
    response = client.post(url(sub, "ship"), {"tracking_number": "<script>", "carrier": ""})
    assert response.status_code == 400
    assert "<script>" not in response.text
    sub.refresh_from_db()
    assert (sub.state, sub.tracking_number) == ("accepted", "")


def test_an_illegal_step_is_refused_with_a_message_and_no_change(
    client: Client,
    owner: User,
    sub: VendorOrder,
) -> None:
    verified_login(client, owner)
    response = client.post(url(sub, "deliver"))
    assert response.status_code == 409
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"
    assert not AuditLog.objects.filter(target_model="orders.vendororder").exists()


def test_actions_are_post_only(client: Client, owner: User, sub: VendorOrder) -> None:
    verified_login(client, owner)
    for action in ("accept", "ship", "deliver"):
        assert client.get(url(sub, action)).status_code == 405
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"


def test_an_unknown_action_url_is_404(client: Client, owner: User, sub: VendorOrder) -> None:
    verified_login(client, owner)
    assert client.post(url(sub, "refund")).status_code == 404


def test_posts_need_a_real_csrf_token(owner: User, sub: VendorOrder) -> None:
    client = Client(enforce_csrf_checks=True)
    verified_login(client, owner)
    assert client.post(url(sub, "accept")).status_code == 403
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"


# --- tenant isolation


def test_vendor_a_never_sees_or_touches_vendor_b_orders(
    client: Client, cross_tenant: CrossTenant
) -> None:
    mine = paid_vendor_order(cross_tenant.a.vendor)
    theirs = paid_vendor_order(cross_tenant.b.vendor, order__customer_name="Zed Zeta")
    verified_login(client, cross_tenant.a.owner)
    listing = client.get(ORDERS)
    assert mine.order.number in listing.text
    for marker in (*cross_tenant.markers(theirs), theirs.order.number, "Zed Zeta"):
        assert marker not in listing.text
    # IDOR: detail and every POST answer 404, and nothing changes or is audited.
    assert client.get(url(theirs)).status_code == 404
    for action in ("accept", "ship", "deliver"):
        data = {"tracking_number": "TRK-12345"}
        assert client.post(url(theirs, action), data).status_code == 404
    assert VendorOrder.objects.get(pk=theirs.pk).state == "pending"
    assert not AuditLog.objects.filter(target_model="orders.vendororder").exists()


def test_a_forged_or_malformed_id_is_404(client: Client, owner: User) -> None:
    verified_login(client, owner)
    assert client.get(f"{ORDERS}{uuid.uuid4()}/").status_code == 404
    assert client.post(f"{ORDERS}{uuid.uuid4()}/accept/").status_code == 404
    assert client.get(f"{ORDERS}not-a-uuid/").status_code == 404


def test_the_gate_still_applies(
    client: Client, customer: User, platform_staff: User, sub: VendorOrder
) -> None:
    verified_login(client, customer)
    assert client.get(ORDERS).status_code == 403
    assert client.post(url(sub, "accept")).status_code == 403
    client = Client()
    verified_login(client, platform_staff)
    assert client.get(ORDERS).status_code == 403

"""ORD.6: the vendor fulfils its own orders from the shop space of the Manager.

List, detail, and the POSTs accept / ship / deliver. Tenant isolation (list, detail, every POST,
forged ids), tracking validation, illegal transitions, audit rows, no buyer e-mail shown.
"""

import datetime as dt
import uuid
import zoneinfo

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.manager.vendor_choice import SESSION_KEY
from apps.orders import services
from apps.orders.engine import tax_retention_until
from apps.orders.models import Order, VendorOrder
from apps.orders.tests.factories import paid_vendor_order, unchecked
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.tenancy import CrossTenant

from .conftest import PICKER, SHOP

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
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


# --- review findings (closed vendor, forged switch, error order, minimisation)


def test_a_closed_vendor_gets_the_closed_page_on_list_detail_and_every_post(
    client: Client, owner: User, vendor: Vendor, sub: VendorOrder
) -> None:
    verified_login(client, owner)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.CLOSED)
    for path in (ORDERS, url(sub)):
        assert client.get(path).status_code == 403
    for action in ("accept", "ship", "deliver"):
        response = client.post(url(sub, action), {"tracking_number": "TRK-12345"})
        assert response.status_code == 403
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"


def test_a_forged_vendor_choice_never_reaches_another_vendors_orders(
    client: Client, cross_tenant: CrossTenant
) -> None:
    theirs = paid_vendor_order(cross_tenant.b.vendor)
    verified_login(client, cross_tenant.a.owner)
    client.get(SHOP)
    session = client.session
    session[SESSION_KEY] = str(cross_tenant.b.vendor.public_id)
    session.save()
    for path in (ORDERS, url(theirs)):
        assert client.get(path).status_code == 404
    assert client.post(url(theirs, "accept")).status_code == 404
    assert VendorOrder.objects.get(pk=theirs.pk).state == "pending"


def test_a_member_of_two_vendors_acts_only_on_the_selected_vendors_orders(
    client: Client, cross_tenant: CrossTenant
) -> None:
    user = cross_tenant.a.owner
    other = VendorMemberFactory.create(vendor=VendorFactory.create(), user=user, role="staff")
    mine = paid_vendor_order(cross_tenant.a.vendor)
    elsewhere = paid_vendor_order(other.vendor)
    verified_login(client, user)
    client.post(PICKER, {"vendor": str(cross_tenant.a.vendor.public_id)})
    assert client.post(url(mine, "accept")).status_code == 302
    assert client.post(url(elsewhere, "accept")).status_code == 404
    assert VendorOrder.objects.get(pk=elsewhere.pk).state == "pending"


def test_an_invalid_ship_form_on_an_order_not_accepted_is_409_not_400(
    client: Client, owner: User, sub: VendorOrder
) -> None:
    verified_login(client, owner)
    response = client.post(url(sub, "ship"), {"tracking_number": ""})
    assert response.status_code == 409
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"


@pytest.mark.parametrize(
    "data",
    [
        {"tracking_number": "x" * 101},
        {"tracking_number": "TRK-12345", "carrier": "<b>Poste</b>"},
        {"tracking_number": "TRK-12345", "carrier": "x" * 101},
    ],
)
def test_a_bad_tracking_or_carrier_is_400_and_changes_nothing(
    client: Client, owner: User, sub: VendorOrder, data: dict[str, str]
) -> None:
    verified_login(client, owner)
    client.post(url(sub, "accept"))
    response = client.post(url(sub, "ship"), data)
    assert response.status_code == 400
    assert "<b>" not in response.text
    sub.refresh_from_db()
    assert (sub.state, sub.tracking_number) == ("accepted", "")


def test_the_address_is_hidden_after_delivery(
    client: Client, owner: User, sub: VendorOrder
) -> None:
    verified_login(client, owner)
    assert "1 Test Street" in client.get(url(sub)).text
    client.post(url(sub, "accept"))
    client.post(url(sub, "ship"), {"tracking_number": "TRK-12345"})
    assert "1 Test Street" in client.get(url(sub)).text
    client.post(url(sub, "deliver"))
    assert "1 Test Street" not in client.get(url(sub)).text


def test_a_restricted_order_renders_without_the_address(
    client: Client, owner: User, sub: VendorOrder
) -> None:
    created = dt.datetime.now(dt.UTC) - dt.timedelta(days=366 * 3)
    until = tax_retention_until(created, zoneinfo.ZoneInfo("Europe/Rome"))
    with unchecked():
        Order.objects.filter(pk=sub.order_id).update(created_at=created, tax_retention_until=until)
    services.restrict_order(Order.objects.get(pk=sub.order_id), ctx=CTX)
    assert Order.objects.get(pk=sub.order_id).privacy_status == "restricted_tax_only"
    verified_login(client, owner)
    response = client.get(url(sub))
    assert response.status_code == 200
    assert "1 Test Street" not in response.text

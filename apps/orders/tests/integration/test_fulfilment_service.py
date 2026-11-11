"""ORD.6: `fulfil_vendor_order`, the vendor's accept / ship / deliver on its own sub-orders.

Role check (the `orders` action), tracking validation, illegal transitions, audit rows,
dry-run, and the roll-up of the order state.
"""

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import engine, services
from apps.orders.models import VendorOrder
from apps.orders.tests.factories import paid_vendor_order
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="vendor:test", source=AuditLog.Source.VENDOR)


def member(sub: VendorOrder, role: str = "staff"):  # type: ignore[no-untyped-def]
    return VendorMemberFactory.create(vendor=sub.vendor, role=role).user


def test_accept_ship_deliver_walks_the_machine_and_rolls_up_the_order() -> None:
    sub = paid_vendor_order()
    actor = member(sub)
    services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    sub.order.refresh_from_db()
    assert (sub.state, sub.order.state) == ("accepted", "in_fulfillment")
    services.fulfil_vendor_order(
        sub, "ship", actor=actor, ctx=CTX, tracking_number=" TRK-12345 ", carrier=" Poste "
    )
    assert (sub.state, sub.tracking_number, sub.carrier) == ("shipped", "TRK-12345", "Poste")
    assert sub.shipped_at is not None
    sub.order.refresh_from_db()
    assert sub.order.state == "fulfilled"
    services.fulfil_vendor_order(sub, "deliver", actor=actor, ctx=CTX)
    assert sub.state == "delivered"
    assert sub.delivered_at is not None


def test_every_step_writes_an_audit_row() -> None:
    sub = paid_vendor_order()
    services.fulfil_vendor_order(sub, "accept", actor=member(sub), ctx=CTX)
    entry = AuditLog.objects.filter(action="orders.transition", target_model="orders.vendororder")
    assert entry.count() == 1
    row = entry.get()
    assert row.before is not None
    assert row.after is not None
    assert (row.before["state"], row.after["state"]) == ("pending", "accepted")


def test_a_non_member_and_another_vendors_member_are_refused() -> None:
    sub = paid_vendor_order()
    other = member(paid_vendor_order(VendorFactory.create()))
    for actor in (UserFactory.create(), other):
        with pytest.raises(PermissionDenied):
            services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"
    assert not AuditLog.objects.filter(target_model="orders.vendororder").exists()


def test_an_inactive_member_is_refused() -> None:
    sub = paid_vendor_order()
    actor = member(sub)
    actor.is_active = False
    actor.save()
    with pytest.raises(PermissionDenied):
        services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)


@pytest.mark.parametrize("action", ["ship", "deliver"])
def test_a_step_out_of_order_is_refused(action: str) -> None:
    sub = paid_vendor_order()
    with pytest.raises(engine.IllegalTransition):
        services.fulfil_vendor_order(
            sub, action, actor=member(sub), ctx=CTX, tracking_number="TRK-12345"
        )
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"


def test_accepting_twice_is_refused() -> None:
    sub = paid_vendor_order()
    actor = member(sub)
    services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    with pytest.raises(engine.IllegalTransition):
        services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)


def test_shipping_needs_a_valid_tracking_number_and_changes_nothing_otherwise() -> None:
    sub = paid_vendor_order()
    actor = member(sub)
    services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    for bad in ("", "<script>", "x" * 101):
        with pytest.raises(engine.InvalidTracking):
            services.fulfil_vendor_order(sub, "ship", actor=actor, ctx=CTX, tracking_number=bad)
    sub.refresh_from_db()
    assert (sub.state, sub.tracking_number, sub.shipped_at) == ("accepted", "", None)


def test_an_unpaid_order_cannot_be_accepted() -> None:
    from apps.orders.tests.factories import VendorOrderFactory

    sub = VendorOrderFactory.create()
    with pytest.raises(ValueError, match="paid"):
        services.fulfil_vendor_order(sub, "accept", actor=member(sub), ctx=CTX)


def test_an_unknown_action_is_refused() -> None:
    sub = paid_vendor_order()
    with pytest.raises(engine.UnknownFulfilmentAction):
        services.fulfil_vendor_order(sub, "refund", actor=member(sub), ctx=CTX)


def test_dry_run_returns_the_changes_and_writes_nothing() -> None:
    sub = paid_vendor_order()
    changes = services.fulfil_vendor_order(sub, "accept", actor=member(sub), ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert VendorOrder.objects.get(pk=sub.pk).state == "pending"
    assert not AuditLog.objects.filter(target_model="orders.vendororder").exists()

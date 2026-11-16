"""ORD.20 (1/3): versions, drafts and the territory checks of order amendments, in the service.

Seed vendor order (spec 07, ORD.20): A 2 x 50.00 and B 1 x 30.00 net at 22 %, shipping 10.00 net,
gross 170.80, 10 % commission rule. Every refusal assertion says in plain words what was expected
and what happened (owner, 2026-11-16: control tests with intelligible answers).
"""

import datetime as dt
import uuid
import zoneinfo
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import amendment_engine as am
from apps.orders import amendments, engine, services
from apps.orders.models import (
    AmendmentState,
    Order,
    OrderAmendment,
    VendorOrder,
    VendorOrderVersion,
)
from apps.orders.services import LineInput, VendorOrderInput
from apps.orders.tests.control import plainly
from apps.orders.tests.factories import (
    OrderAmendmentFactory,
    VendorOrderVersionFactory,
    unchecked,
)
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.tax import engine as tax
from apps.tax.tests.factories import TaxRateFactory, VendorTaxProfileFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

D = Decimal
NOW = dt.datetime(2027, 3, 5, 10, 0, tzinfo=dt.UTC)
CTX = AuditContext(actor_label="vendor:test", source=AuditLog.Source.VENDOR)
ROME = {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "net_goods",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross_paid",
}


# TAX.6 validity: the standard rate goes from 22 % to 23 % at this time (test `rate_change`).
RATE_CHANGE = utc(2027, 3, 3)


@pytest.fixture(autouse=True)
def tax_setup(request: pytest.FixtureRequest) -> None:
    PlatformOperatorFactory.create(open_ended=True, valid_from=utc(2026, 1, 1), country="IT")
    if request.node.get_closest_marker("rate_change") is None:
        TaxRateFactory.create(
            country="IT",
            category=tax.STANDARD,
            rate=D("22"),
            valid_from=utc(2026, 1, 1),
            open_ended=True,
        )
        return
    TaxRateFactory.create(
        country="IT",
        category=tax.STANDARD,
        rate=D("22"),
        valid_from=utc(2026, 1, 1),
        valid_to=RATE_CHANGE,
    )
    TaxRateFactory.create(
        country="IT", category=tax.STANDARD, rate=D("23"), valid_from=RATE_CHANGE, open_ended=True
    )


def method(
    seller: Vendor,
    key: str,
    cost: str,
    max_days: int,
    *,
    default: bool = False,
    countries: tuple[str, ...] = ("IT",),
) -> None:
    """A method of the vendor's zone (one zone per vendor, shared by its methods)."""
    existing = ShippingMethod.objects.filter(vendor=seller).first()
    found = ShippingMethodFactory.create(
        vendor=seller,
        zone=existing.zone if existing else ShippingZoneFactory.create(countries=list(countries)),
        key=key,
        is_default=default,
        min_delivery_days=1,
        max_delivery_days=max_days,
    )
    ShippingMethodTranslationFactory.create(method=found, name=key.title(), status="published")
    WeightBandFactory.create(method=found, from_kg=D("0"), to_kg=D("30"), cost_net=D(cost))


def seller(countries: tuple[str, ...] = ("IT",)) -> Vendor:
    """An Italian vendor without OSS (domestic_only) with one 10.00 method, 5 days."""
    found = VendorFactory.create(status=Vendor.Status.ACTIVE, country="IT", sells_online=True)
    VendorTaxProfileFactory.create(vendor=found, valid_from=utc(2026, 1, 1))
    method(found, "standard", "10.00", 5, default=True, countries=countries)
    return found


def product(owner: Vendor, price: str) -> Product:
    return ProductFactory.create(
        vendor=owner,
        status=Product.Status.PUBLISHED,
        price_net=D(price),
        weight_kg=D("0.500"),
        fiscal_category=tax.STANDARD,
    )


def seed(
    owner: Vendor | None = None, *, pay: bool = True, address: dict[str, str] | None = None
) -> VendorOrder:
    """The ORD.20 seed order, paid (or only placed, awaiting payment), delivered to `address`
    (Rome by default). Lines carry the fiscal-category and weight snapshots of `place_order`."""
    owner = owner or seller()
    address = address or ROME
    a, b = product(owner, "50.00"), product(owner, "30.00")
    snap = {"fiscal_category": tax.STANDARD, "weight_kg": D("0.500")}
    reason = tax.REASON_DOMESTIC_VENDOR_COUNTRY
    lines = [
        LineInput(a, "SKU-A", "A", D("50.00"), 2, D("22"), reason, **snap),  # type: ignore[arg-type]
        LineInput(b, "SKU-B", "B", D("30.00"), 1, D("22"), reason, **snap),  # type: ignore[arg-type]
    ]
    order, _ = services.create_order(
        email="buyer@example.com",
        language="en",
        billing_address=address,
        billing_country=address["country"],
        shipping_address=address,
        shipping_country=address["country"],
        vendor_orders=[
            VendorOrderInput(
                vendor=owner,
                lines=lines,
                shipping_net=D("10.00"),
                shipping_tax_rate=D("22"),
                shipping_tax_reason=tax.REASON_DOMESTIC_VENDOR_COUNTRY,
                shipping_choice={"key": "standard", "label": "Standard", "max_days": 5},
                commission_snapshot=COMMISSION,
            )
        ],
        ctx=CTX,
    )
    assert order is not None
    services.transition_order(order, "awaiting_payment", ctx=CTX)
    if pay:
        services.transition_order(order, "paid", ctx=CTX)
    return order.vendor_orders.get()


def backdate(order: Order, created: dt.datetime) -> None:
    """Set created_at (and its retention date) without the triggers (test set-up)."""
    until = engine.tax_retention_until(created, zoneinfo.ZoneInfo("Europe/Rome"))
    with unchecked():
        Order.objects.filter(pk=order.pk).update(created_at=created, tax_retention_until=until)
    order.refresh_from_db()


def owner_of(sub: VendorOrder) -> Any:
    return VendorMemberFactory.create(vendor=sub.vendor, role="owner").user


def key_of(sub: VendorOrder, sku: str) -> str:
    return str(sub.lines.get(sku=sku).public_id)


def draft(sub: VendorOrder, *changes: am.AmendmentChange, **kwargs: Any) -> Any:
    kwargs.setdefault("actor", owner_of(sub))
    kwargs.setdefault("reason", "Out of stock at the supplier")
    return amendments.draft_amendment(sub, list(changes), ctx=CTX, now=NOW, **kwargs)


def move_to(country: str, postcode: str) -> am.ChangeAddress:
    return am.ChangeAddress(
        address={"line1": "2 Via Nuova", "city": "X", "postcode": postcode, "country": country},
        requested_by_customer=True,
    )


CUSTOMER_ASKED = {"channel": "email", "date": "2027-03-04", "text": "Please fix my address"}


# --- versions ---------------------------------------------------------------------------------


def test_v1_is_written_when_the_payment_succeeds_dated_on_the_payment_day() -> None:
    sub = seed(pay=False)
    assert not sub.versions.exists(), "an unpaid order must have no version yet"
    order = sub.order
    created = timezone.now() - dt.timedelta(days=1)
    backdate(order, created)  # placed on day D at the snapshot rate of 22 %
    services.transition_order(order, "paid", ctx=CTX)  # paid on D+1
    order.refresh_from_db()
    assert order.paid_at is not None
    version = sub.versions.get()
    paid_day = order.paid_at.astimezone(zoneinfo.ZoneInfo("Europe/Rome")).date()
    created_day = created.astimezone(zoneinfo.ZoneInfo("Europe/Rome")).date()
    snapshot = am.VersionSnapshot.from_json(version.snapshot)
    portions = [p for line in snapshot.lines for p in line.portions]
    portions += list(snapshot.shipping.portions)
    assert {p.tax_on for p in portions} == {paid_day}, (
        f"v1 must be taxed on the payment day {paid_day}, not the creation day {created_day}, "
        f"but its portions carry {sorted({p.tax_on for p in portions})}"
    )
    assert {p.rate for p in portions} == {D("22.000")}, (
        "v1 keeps the rates the checkout snapshotted, never re-rated on the payment day"
    )


def test_placing_an_order_writes_v1_with_the_seed_totals() -> None:
    sub = seed()
    version = sub.versions.get()
    assert (version.number, version.gross_total) == (1, D("170.80")), (
        "the placed order must be v1 with gross 170.80, "
        f"got v{version.number} {version.gross_total}"
    )
    assert sub.current_version_id == version.pk
    snapshot = am.VersionSnapshot.from_json(version.snapshot)
    assert snapshot.totals.gross_total == D("170.80")
    assert {line.line_key for line in snapshot.lines} == {
        key_of(sub, "SKU-A"),
        key_of(sub, "SKU-B"),
    }


def test_v1_rows_cannot_be_updated_or_deleted() -> None:
    version = seed().versions.get()
    for sql in (
        "UPDATE orders_vendororderversion SET gross_total = 1 WHERE id = %s",
        "UPDATE orders_vendororderversion SET snapshot = '{}'::jsonb WHERE id = %s",
        "UPDATE orders_vendororderversion SET shipping_address = '{}'::jsonb WHERE id = %s",
        "DELETE FROM orders_vendororderversion WHERE id = %s",
    ):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, [version.pk])
    with pytest.raises(PermissionError):
        version.save()


# --- drafting: the regression numbers ----------------------------------------------------------


def test_removing_b_drafts_a_minor_change_refunding_36_60() -> None:
    sub = seed()
    amendment, changes = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    assert amendment.kind == "minor", (
        "removing an unavailable line must be minor, "
        f"but it was {amendment.kind}: {amendment.reasons}"
    )
    assert amendment.preview["refund"] == "36.60", (
        f"removing B must refund 36.60, the draft says {amendment.preview['refund']}"
    )
    assert amendment.preview["commission_delta"] == "-3.00"
    assert amendment.state == AmendmentState.DRAFT
    assert amendment.from_version_id == sub.current_version_id
    assert AuditLog.objects.filter(action="orders.amendment.draft").count() == 1
    assert not changes.is_empty


def test_lowering_a_to_45_drafts_a_refund_of_12_20() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.ChangePrice(key_of(sub, "SKU-A"), D("45.00")))
    assert amendment is not None
    assert (amendment.preview["refund"], amendment.preview["commission_delta"]) == (
        "12.20",
        "-1.00",
    )


def test_the_dry_run_moves_nothing() -> None:
    sub = seed()
    amendment, changes = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), dry_run=True)
    assert amendment is None
    assert not changes.is_empty, "the dry run must still show what would change"
    assert not OrderAmendment.objects.exists(), "a dry run must write no amendment"
    assert not AuditLog.objects.filter(action__startswith="orders.amendment").exists()
    assert VendorOrderVersion.objects.filter(vendor_order=sub).count() == 1


# --- refusals ---------------------------------------------------------------------------------


def test_a_second_draft_on_the_same_package_is_refused() -> None:
    sub = seed()
    draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    with pytest.raises(amendments.AmendmentRefused) as refused:
        draft(sub, am.ChangePrice(key_of(sub, "SKU-A"), D("45.00")))
    assert "already has an open amendment" in str(refused.value), (
        f"a second draft must be refused because one is open, but the message was {refused.value}"
    )
    assert OrderAmendment.objects.count() == 1


def test_the_database_allows_one_open_amendment_per_package() -> None:
    sub = seed()
    first, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert first is not None
    first.pk = None
    first.public_id = uuid.uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        first.save()


def test_a_shipped_package_cannot_be_amended() -> None:
    sub = seed()
    actor = owner_of(sub)
    services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    services.fulfil_vendor_order(sub, "ship", actor=actor, ctx=CTX, tracking_number="TRK-123")
    with pytest.raises(amendments.AmendmentRefused) as refused:
        draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert "only before shipping" in str(refused.value), (
        "a shipped package must be refused because it has shipped, "
        f"but the message was {refused.value}"
    )


def test_an_open_amendment_blocks_shipping() -> None:
    sub = seed()
    actor = owner_of(sub)
    services.fulfil_vendor_order(sub, "accept", actor=actor, ctx=CTX)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    with pytest.raises(services.AmendmentOpen) as refused:
        services.fulfil_vendor_order(sub, "ship", actor=actor, ctx=CTX, tracking_number="TRK-123")
    assert str(amendment.public_id) in str(refused.value), (
        f"the ship refusal must name the open amendment, but it said {refused.value}"
    )
    assert VendorOrder.objects.get(pk=sub.pk).state == "accepted"
    amendments.discard_amendment(amendment, actor=actor, ctx=CTX)
    services.fulfil_vendor_order(sub, "ship", actor=actor, ctx=CTX, tracking_number="TRK-123")
    assert VendorOrder.objects.get(pk=sub.pk).state == "shipped"


def test_a_member_of_shop_a_amending_shop_b_gets_404() -> None:
    sub_b = seed()
    member_a = VendorMemberFactory.create(vendor=seller(), role="owner").user
    with pytest.raises(Http404):
        draft(sub_b, am.RemoveLine(key_of(sub_b, "SKU-B")), actor=member_a)
    assert not OrderAmendment.objects.exists()


def test_a_reason_is_required() -> None:
    sub = seed()
    with pytest.raises(amendments.AmendmentRefused, match="reason"):
        draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), reason="  ")


def test_an_unpaid_order_cannot_be_amended() -> None:
    owner = seller()
    a = product(owner, "10.00")
    order, _ = services.create_order(
        email="buyer@example.com",
        language="en",
        billing_address=ROME,
        billing_country="IT",
        shipping_address=ROME,
        shipping_country="IT",
        vendor_orders=[
            VendorOrderInput(
                vendor=owner,
                lines=[LineInput(a, "S", "S", D("10.00"), 1, D("22"), "domestic_vendor_country")],
                commission_snapshot=COMMISSION,
            )
        ],
        ctx=CTX,
    )
    assert order is not None
    unpaid = order.vendor_orders.get()
    with pytest.raises(amendments.AmendmentRefused, match="not paid"):
        draft(unpaid, am.ChangeQuantity(key_of(unpaid, "S"), 1))


# --- territory checks -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "postcode", "place"),
    [
        ("ES", "35001", "Canary Islands"),
        ("IT", "23041", "Livigno"),
        ("IT", "22061", "Campione d'Italia"),
        ("CH", "8001", "CH"),
        ("GB", "BT1 1AA", "Northern Ireland"),
    ],
)
def test_a_move_to_an_unsupported_destination_is_refused_and_never_drafted(
    country: str, postcode: str, place: str
) -> None:
    sub = seed()
    with pytest.raises(amendments.AmendmentBlocked) as refused:
        draft(sub, move_to(country, postcode), customer_request=CUSTOMER_ASKED)
    message = str(refused.value)
    assert place in message, f"the refusal must name {place}, but it was {message!r}"
    assert "cannot be changed" in message, (
        f"a move to {country} {postcode} must be refused naming {place}, but the message was "
        f"{message!r}"
    )
    assert not OrderAmendment.objects.exists(), "a blocked destination must never be drafted"


def test_a_customer_requested_house_number_fix_is_a_minor_draft() -> None:
    sub = seed()
    amendment, _ = draft(sub, move_to("IT", "00100"), customer_request=CUSTOMER_ASKED)
    assert amendment is not None
    assert amendment.kind == "minor", (
        f"a house-number fix asked by the customer must be minor, but it was {amendment.kind}: "
        f"{amendment.reasons}"
    )
    assert amendment.customer_request == CUSTOMER_ASKED


def test_an_address_fix_asked_by_the_customer_needs_the_request() -> None:
    sub = seed()
    with pytest.raises(amendments.AmendmentRefused, match="channel"):
        draft(sub, move_to("IT", "00100"))


def test_the_audit_trail_keeps_no_address() -> None:
    sub = seed()
    draft(sub, move_to("IT", "00100"), customer_request=CUSTOMER_ASKED)
    entry = AuditLog.objects.get(action="orders.amendment.draft")
    assert "2 Via Nuova" not in str(entry.after)


# --- applying: carriers and the stale version ------------------------------------------------


def test_a_dearer_carrier_is_applied_as_v2_and_moves_no_money() -> None:
    sub = seed()
    method(sub.vendor, "express", "14.00", 7)
    actor = owner_of(sub)
    amendment, _ = draft(
        sub, am.ChangeCarrier(am.ShippingOffer("express", "", D("0"))), actor=actor
    )
    assert amendment is not None
    assert amendment.kind == "minor", (
        f"a dearer carrier absorbed by the vendor with +2 days must be minor, but it was "
        f"{amendment.kind}: {amendment.reasons}"
    )
    assert (amendment.preview["money_delta"], amendment.preview["vendor_absorbs"]) == (
        "0.00",
        "4.00",
    )
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    sub.refresh_from_db()
    v2 = sub.versions.get(number=2)
    assert sub.current_version_id == v2.pk
    assert v2.gross_total == D("170.80")
    assert v2.shipping_address is not None
    assert v2.shipping_address["postcode"] == "00100"
    assert amendment.state == AmendmentState.APPLIED


def test_anonymisation_replaces_a_v2_address_and_nothing_else_may_touch_it() -> None:
    sub = seed()
    method(sub.vendor, "express", "14.00", 7)
    actor = owner_of(sub)
    amendment, _ = draft(
        sub, am.ChangeCarrier(am.ShippingOffer("express", "", D("0"))), actor=actor
    )
    assert amendment is not None
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    v2 = sub.versions.get(number=2)
    placeholder = engine.ANONYMISED_ORDER_FIELDS["shipping_address"]
    # Outside the scrub, even the placeholder is refused: the version is append-only.
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrderVersion.objects.filter(pk=v2.pk).update(shipping_address=placeholder)
    before = VendorOrderVersion.objects.filter(pk=v2.pk).values(
        "snapshot", "gross_total", "number", "amendment_id"
    )[0]
    order = sub.order
    backdate(order, timezone.now() - dt.timedelta(days=365 * 12))  # retention over
    services.anonymise_order(order, ctx=CTX)
    v2 = VendorOrderVersion.objects.get(pk=v2.pk)
    assert v2.shipping_address == placeholder, (
        "anonymisation must replace the package's own v2 address with the placeholder, "
        f"but it is {v2.shipping_address}"
    )
    after = VendorOrderVersion.objects.filter(pk=v2.pk).values(
        "snapshot", "gross_total", "number", "amendment_id"
    )[0]
    assert after == before, "anonymisation must change nothing but the address of a version"
    assert sub.versions.get(number=1).shipping_address is None
    assert "2 Via Nuova" not in str(
        AuditLog.objects.filter(action="orders.anonymise").values_list("after", flat=True)
    )


def test_a_carrier_three_days_slower_is_substantial() -> None:
    sub = seed()
    method(sub.vendor, "slow", "10.00", 8)
    amendment, _ = draft(sub, am.ChangeCarrier(am.ShippingOffer("slow", "", D("0"))))
    assert amendment is not None
    assert amendment.kind == "substantial", (
        "a carrier at the same price with max delivery +3 days must be substantial, but it was "
        "classified minor"
    )


def test_an_apply_on_a_stale_version_is_superseded_and_moves_nothing() -> None:
    sub = seed()
    method(sub.vendor, "express", "10.00", 5)
    actor = owner_of(sub)
    amendment, _ = draft(
        sub, am.ChangeCarrier(am.ShippingOffer("express", "", D("0"))), actor=actor
    )
    assert amendment is not None
    # Another path moved the version under the draft (set-up: a sibling applied amendment).
    other = OrderAmendmentFactory.create(
        from_version=amendment.from_version, state=AmendmentState.APPLIED
    )
    v1 = amendment.from_version
    moved = VendorOrderVersionFactory.create(
        vendor_order=sub,
        number=2,
        amendment=other,
        snapshot=v1.snapshot,
        goods_net=v1.goods_net,
        shipping_net=v1.shipping_net,
        tax_total=v1.tax_total,
        gross_total=v1.gross_total,
    )
    VendorOrder.objects.filter(pk=sub.pk).update(current_version=moved)
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    assert amendment.state == AmendmentState.SUPERSEDED, (
        f"an apply on a stale version must become superseded, but it is {amendment.state}"
    )
    assert VendorOrderVersion.objects.filter(vendor_order=sub).count() == 2, (
        "a superseded apply must write no version"
    )


def test_an_apply_that_moves_money_waits_for_part_two() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    with pytest.raises(amendments.AmendmentRefused, match="moves money"):
        amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    assert VendorOrderVersion.objects.filter(vendor_order=sub).count() == 1


def test_staff_cannot_change_prices() -> None:
    from django.contrib.auth.models import Permission

    from apps.accounts.tests.factories import UserFactory

    sub = seed()
    staff = UserFactory.create(is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_all_vendors"))
    with pytest.raises(amendments.AmendmentRefused, match="prices"):
        draft(
            sub,
            am.ChangePrice(key_of(sub, "SKU-A"), D("45.00")),
            actor=staff,
            staff_note="customer called",
        )


# --- second review of #551 ------------------------------------------------------------------


def test_v1_is_in_the_audit_entry_of_the_payment() -> None:
    sub = seed()
    entry = AuditLog.objects.get(
        action="orders.transition",
        target_model="orders.vendororderversion",
        target_key=str(sub.versions.get().public_id),
    )
    assert entry.after is not None
    assert entry.after["gross_total"] in ("170.80", D("170.80"), 170.8), plainly(
        "the payment's audit entry must record v1 with gross 170.80", f"it recorded {entry.after}"
    )


@pytest.mark.rate_change
def test_a_rate_changed_by_law_after_the_order_never_re_rates_the_paid_portion() -> None:
    sub = seed()  # placed and paid at 22 % (now), before RATE_CHANGE
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))  # NOW is after RATE_CHANGE
    assert amendment is not None
    assert amendment.preview["refund"] == "36.60", plainly(
        "removing B after the rate went from 22 % to 23 % must still refund 36.60",
        f"the draft refunds {amendment.preview['refund']}",
    )
    reversed_b = [
        m for m in amendment.preview["tax_movements"] if m["part"] == key_of(sub, "SKU-B")
    ]
    assert [(m["tax"], m["rate"]) for m in reversed_b] == [("-6.60", "22.000")], plainly(
        "B's VAT must be reversed at 22 %, the rate it was paid at", f"it moved {reversed_b}"
    )
    after = am.VersionSnapshot.from_json(sub.versions.get().snapshot)  # v1, still current
    a_line = next(line for line in after.lines if line.sku == "SKU-A")
    assert [p.rate for p in a_line.portions] == [D("22.000")]


def test_staff_cannot_change_the_delivery_time_or_choose_a_dearer_carrier() -> None:
    from django.contrib.auth.models import Permission

    from apps.accounts.tests.factories import UserFactory

    sub = seed()
    method(sub.vendor, "express", "14.00", 6)
    staff = UserFactory.create(is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_all_vendors"))
    with pytest.raises(amendments.AmendmentRefused) as refused:
        draft(sub, am.ChangeDeliveryTime(9), actor=staff, staff_note="customer called")
    assert "delivery times" in str(refused.value), plainly(
        "staff changing the delivery time must be refused naming delivery times",
        f"the message was {refused.value}",
    )
    with pytest.raises(amendments.AmendmentRefused) as refused:
        draft(
            sub,
            am.ChangeCarrier(am.ShippingOffer("express", "", D("0"))),
            actor=staff,
            staff_note="customer called",
        )
    assert "same or a lower price" in str(refused.value), plainly(
        "staff choosing a dearer carrier must be refused with the same-or-lower-price rule",
        f"the message was {refused.value}",
    )
    assert not OrderAmendment.objects.exists()


NORTHERN_IRELAND = {
    "line1": "1 High Street",
    "city": "Belfast",
    "postcode": "BT1 1AA",
    "country": "GB",
}


def ni_seller() -> Vendor:
    """A vendor without OSS shipping to IT and GB, with a fixed price (no surcharge) for the
    seeded Northern Ireland special area instead of the default quote."""
    from apps.shipping import engine as shipping_engine
    from apps.shipping.models import SpecialArea
    from apps.shipping.tests.factories import SpecialAreaTreatmentFactory

    found = seller(countries=("IT", "GB"))
    SpecialAreaTreatmentFactory.create(
        method=ShippingMethod.objects.get(vendor=found),
        area=SpecialArea.objects.get(code="gb-northern-ireland"),
        mode=shipping_engine.SURCHARGE,
        surcharge_type=shipping_engine.FIXED,
        surcharge=D("0.00"),
    )
    return found


def test_a_house_number_fix_inside_northern_ireland_passes() -> None:
    sub = seed(ni_seller(), address=NORTHERN_IRELAND)
    amendment, _ = draft(sub, move_to("GB", "BT1 2AB"), customer_request=CUSTOMER_ASKED)
    assert amendment is not None
    assert amendment.kind == "minor", plainly(
        "a house-number fix inside Northern Ireland asked by the customer must be minor",
        f"it was {amendment.kind}: {amendment.reasons}",
    )


def test_a_move_to_northern_ireland_follows_the_checkout_rules() -> None:
    """Checkout lets a vendor without OSS sell to XI at its own rate (tax engine, TAX.18), so the
    amendment allows it too: proposed as a substantial change, never blocked."""
    sub = seed(ni_seller())
    amendment, _ = draft(sub, move_to("GB", "BT1 1AA"), customer_request=CUSTOMER_ASKED)
    assert amendment is not None, "a move the checkout allows must be drafted"
    assert amendment.kind == "substantial", plainly(
        "a move from IT to Northern Ireland must be substantial", f"it was {amendment.kind}"
    )


def test_v1_of_an_oss_sale_to_northern_ireland_is_taxed_in_xi() -> None:
    from apps.orders import versions

    sub = seed(seller(countries=("IT", "GB")), address=NORTHERN_IRELAND, pay=False)
    line = sub.lines.get(sku="SKU-A")
    country = versions._country_of_taxation(D("20"), tax.REASON_STANDARD_DESTINATION, sub)
    assert country == "XI", plainly(
        "an OSS sale to a BT postcode must be taxed in XI", f"it was taxed in {country}"
    )
    assert line.fiscal_category == tax.STANDARD


def test_anonymisation_scrubs_the_address_and_words_kept_on_amendments() -> None:
    sub = seed()
    amendment, _ = draft(sub, move_to("IT", "00100"), customer_request=CUSTOMER_ASKED)
    assert amendment is not None
    backdate(sub.order, timezone.now() - dt.timedelta(days=365 * 12))
    services.anonymise_order(sub.order, ctx=CTX)
    amendment.refresh_from_db()
    address = next(c["address"] for c in amendment.changes if c["kind"] == "address")
    assert address == {"anonymised": True, "country": "IT"}, plainly(
        "anonymisation must replace the new address kept in the amendment's changes",
        f"it is {address}",
    )
    assert amendment.customer_request["text"] == engine.ANONYMISED, plainly(
        "anonymisation must replace the customer's own words",
        f"they are {amendment.customer_request['text']!r}",
    )
    assert amendment.customer_request["channel"] == "email"


def test_a_closed_amendment_is_frozen() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    amendments.discard_amendment(amendment, actor=actor, ctx=CTX)
    amendment.reason = "rewritten later"
    with pytest.raises(PermissionError):
        amendment.save()
    with pytest.raises(PermissionError):
        amendment.delete()
    assert OrderAmendment.objects.get(pk=amendment.pk).reason == "Out of stock at the supplier"

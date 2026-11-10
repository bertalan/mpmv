"""ORD.2, owner decision D-29: fiscal snapshot fields are immutable; only the audited scrub
service (restrict after the active phase, anonymise after year 10) may change them."""

import datetime as dt
import zoneinfo
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import engine, services
from apps.orders.models import Order, VendorOrder
from apps.orders.services import LineInput, ScrubNotDue, VendorOrderInput
from apps.orders.tests.factories import OrderFactory, VendorOrderFactory, unchecked
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
PLACED = dt.datetime(2027, 3, 15, 10, 0, tzinfo=dt.UTC)
COMMISSION = {
    "rule_id": "default",
    "version": 1,
    "percent": "10.00",
    "base": "goods",
    "fee_percent": "1.40",
    "fee_fixed": "0.25",
    "fee_base": "gross",
}


def run_sql(*statements: tuple[str, list[Any]]) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        for sql, params in statements:
            cursor.execute(sql, params)


def ago(**delta: int) -> dt.datetime:
    return timezone.now() - dt.timedelta(**delta)


def backdate(order: Order, created: dt.datetime) -> None:
    """Set created_at without the triggers (test set-up): the retention dates count from it,
    and the database checks them against its own clock."""
    until = engine.tax_retention_until(created, zoneinfo.ZoneInfo("Europe/Rome"))
    with unchecked():
        Order.objects.filter(pk=order.pk).update(created_at=created, tax_retention_until=until)
    order.refresh_from_db()


def place(created: dt.datetime | None = None, **overrides: object) -> Order:
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
    )
    values: dict[str, object] = {
        "email": "buyer@example.com",
        "language": "it",
        "billing_address": {"line1": "1 Via Test", "city": "Roma"},
        "billing_country": "IT",
        "shipping_address": {"line1": "2 Via Test", "city": "Milano"},
        "shipping_country": "IT",
        "customer_name": "Mario Rossi",
        "customer_vat_number": "IT12345678901",
        "customer_tax_id": "RSSMRA80A01H501U",
        "vat_validation": {
            "consultation": "WAPIAAAA",
            "at": "2027-03-15T10:00:00Z",
            "name": "ROSSI SRL",
        },
        "vendor_orders": [sub],
        "ctx": CTX,
        "now": PLACED,
    }
    values.update(overrides)
    order, _ = services.create_order(**values)  # type: ignore[arg-type]
    assert order is not None
    if created is not None:
        backdate(order, created)
    return order


# --- MIG.4: legacy orders may be inserted in any state ----------------------------------------


def test_a_legacy_order_may_be_created_in_a_final_state() -> None:
    order = place(state="closed", legacy_wp_order_id=4711)
    assert Order.objects.get(pk=order.pk).state == "closed"


def test_a_legacy_sub_order_may_be_created_in_a_final_state() -> None:
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
        state="completed",
    )
    order = place(state="closed", legacy_wp_order_id=4712, vendor_orders=[sub])
    assert order.vendor_orders.get().state == "completed"


def test_a_non_legacy_order_keeps_the_initial_state_rule() -> None:
    with pytest.raises(ValueError, match="initial state"):
        place(state="closed")
    sub = VendorOrderInput(
        vendor=VendorFactory.create(),
        lines=[
            LineInput(
                product=None,
                sku="S1",
                title="Cup",
                unit_price_net=Decimal("10.00"),
                quantity=1,
                tax_rate=Decimal("22"),
                tax_reason="domestic",
            )
        ],
        commission_snapshot=COMMISSION,
        state="completed",
    )
    with pytest.raises(ValueError, match="legacy"):
        place(vendor_orders=[sub])


def test_the_database_accepts_a_final_state_only_with_a_legacy_id() -> None:
    kwargs = {"number": "2998-000101", "email": "a@example.com", "billing_country": "IT",
              "shipping_country": "IT", "goods_net": 0, "shipping_net": 0, "tax_total": 0,
              "grand_total": 0}  # fmt: skip
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.bulk_create([Order(**kwargs, state="closed")])
    Order.objects.bulk_create([Order(**kwargs, state="closed", legacy_wp_order_id=99)])


def test_the_save_guard_follows_the_same_legacy_rule() -> None:
    order = OrderFactory.build(state="closed", legacy_wp_order_id=5)
    order.save()
    assert Order.objects.get(pk=order.pk).state == "closed"


# --- D-29: the fiscal fields are immutable ----------------------------------------------------


FISCAL = {
    "email": "other@example.com",
    "customer_name": "Someone Else",
    "customer_vat_number": "IT00000000000",
    "customer_tax_id": "XXXXXXXXXXXXXXXX",
    "billing_address": {"line1": "other"},
    "shipping_address": {"line1": "other"},
    "billing_country": "DE",
    "shipping_country": "DE",
    "vat_validation": {"consultation": "FAKE"},
}


@pytest.mark.parametrize(("column", "value"), list(FISCAL.items()))
def test_a_direct_update_of_a_fiscal_field_fails(column: str, value: object) -> None:
    order = place()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(**{column: value})


def test_raw_sql_cannot_change_an_address_either() -> None:
    order = place()
    with pytest.raises(IntegrityError):
        run_sql(("UPDATE orders_order SET billing_address = '{}' WHERE id = %s", [order.pk]))


@pytest.mark.parametrize(
    ("mode", "assignment"),
    [
        ("restrict", "email = 'x@example.com'"),
        ("anonymise", "grand_total = 1"),
        ("anonymise", "shipping_country = 'DE'"),
    ],
)
def test_a_client_setting_the_scrub_mode_does_not_unlock_other_columns(
    mode: str, assignment: str
) -> None:
    order = place()
    with pytest.raises(IntegrityError):
        run_sql(
            ("SELECT set_config('mpmv.order_scrub', %s, true)", [mode]),
            (f"UPDATE orders_order SET {assignment} WHERE id = %s", [order.pk]),  # noqa: S608
        )


def test_orders_and_sub_orders_cannot_be_deleted() -> None:
    sub = VendorOrderFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        VendorOrder.objects.filter(pk=sub.pk).delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=sub.order_id).delete()


# --- the scrub service ------------------------------------------------------------------------

OLD = dt.datetime(2015, 6, 1, 12, 0, tzinfo=dt.UTC)  # past 31 Dec of year 10
MID = dt.datetime(2024, 1, 15, 12, 0, tzinfo=dt.UTC)  # past 24 months, far from year 10


def with_phone(order: Order) -> VendorOrder:
    sub = order.vendor_orders.get()
    VendorOrder.objects.filter(pk=sub.pk).update(
        carrier_contact_phone="+39 0000 000", support_notes="called twice"
    )
    return VendorOrder.objects.get(pk=sub.pk)


def test_restrict_before_the_active_phase_is_refused() -> None:
    order = place(created=ago(days=700))
    with pytest.raises(ScrubNotDue):
        services.restrict_order(order, ctx=CTX)
    with pytest.raises(ScrubNotDue):
        services.restrict_order(order, ctx=CTX, dry_run=True)


def test_restrict_drops_only_the_non_fiscal_fields_and_audits() -> None:
    order = place(created=MID)
    with_phone(order)
    changeset = services.restrict_order(order, ctx=CTX)
    assert not changeset.is_empty
    sub = order.vendor_orders.get()
    assert (sub.carrier_contact_phone, sub.support_notes) == ("", "")
    order.refresh_from_db()
    assert order.restricted_at is not None
    assert order.email == "buyer@example.com"  # fiscal data stays
    assert order.billing_address["city"] == "Roma"
    entries = AuditLog.objects.filter(action="orders.restrict")
    assert entries.count() == 2  # the sub-order and the order
    assert all("+39" not in str(e.before) + str(e.after) for e in entries)


def test_the_active_phase_setting_can_only_lengthen_it(settings: object) -> None:
    order = place(created=ago(days=30 * 30))  # about 30 months
    settings.ORDER_ACTIVE_PHASE_MONTHS = 36  # type: ignore[attr-defined]
    with pytest.raises(ScrubNotDue):
        services.restrict_order(order, ctx=CTX)
    settings.ORDER_ACTIVE_PHASE_MONTHS = 6  # type: ignore[attr-defined]  # shorter: ignored
    young = place(created=ago(days=200))
    with pytest.raises(ScrubNotDue):
        services.restrict_order(young, ctx=CTX)
    settings.ORDER_ACTIVE_PHASE_MONTHS = 24  # type: ignore[attr-defined]
    services.restrict_order(order, ctx=CTX)


def test_a_dry_run_scrub_writes_nothing() -> None:
    order = place(created=OLD)
    with_phone(order)
    audited = AuditLog.objects.count()
    changeset = services.restrict_order(order, ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    assert order.vendor_orders.get().carrier_contact_phone == "+39 0000 000"
    order.refresh_from_db()
    assert order.restricted_at is None
    assert AuditLog.objects.count() == audited

    changeset = services.anonymise_order(order, ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    order.refresh_from_db()
    assert order.email == "buyer@example.com"
    assert AuditLog.objects.count() == audited


def test_anonymise_before_year_ten_is_refused() -> None:
    order = place(created=MID)
    for state in ("awaiting_payment", "paid"):
        services.transition_order(order, state, ctx=CTX)
    with pytest.raises(ScrubNotDue):
        services.anonymise_order(order, ctx=CTX)
    order.refresh_from_db()
    assert order.anonymised_at is None


def test_anonymise_replaces_personal_fields_and_keeps_money_country_and_rate() -> None:
    order = place(created=OLD)
    with_phone(order)
    before_money = (order.goods_net, order.tax_total, order.grand_total, order.shipping_country)
    services.anonymise_order(order, ctx=CTX)
    order.refresh_from_db()
    assert order.anonymised_at is not None
    assert order.email == engine.ANONYMISED_EMAIL
    assert order.customer_name == engine.ANONYMISED
    assert "Mario" not in str((order.billing_address, order.shipping_address, order.vat_validation))
    assert order.customer is None
    assert (order.goods_net, order.tax_total, order.grand_total, order.shipping_country) == (
        before_money
    )
    sub = order.vendor_orders.get()
    assert sub.carrier_contact_phone == ""
    assert sub.lines.get().tax_rate == Decimal("22.000")
    entries = AuditLog.objects.filter(action="orders.anonymise")
    assert entries.exists()
    assert all("Rossi" not in str(e.before) + str(e.after) for e in entries)
    assert services.anonymise_order(order, ctx=CTX).is_empty  # idempotent


def test_after_anonymisation_the_personal_fields_are_immutable_again() -> None:
    order = place(created=OLD)
    services.anonymise_order(order, ctx=CTX)
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(email="back@example.com")


# --- the trigger enforces the gates itself (raw SQL) --------------------------------------------


def scrub_sql(order: Order, mode: str, assignment: str, *params: Any) -> None:
    run_sql(
        ("SELECT set_config('mpmv.order_scrub', %s, true)", [mode]),
        (f"UPDATE orders_order SET {assignment} WHERE id = %s", [*params, order.pk]),  # noqa: S608
    )


def test_raw_anonymise_on_a_young_order_fails() -> None:
    order = place()
    with pytest.raises(IntegrityError):
        scrub_sql(order, "anonymise", "email = %s", engine.ANONYMISED_EMAIL)


def test_raw_anonymise_with_a_wrong_placeholder_fails_even_on_an_old_order() -> None:
    order = place(created=OLD)
    with pytest.raises(IntegrityError):
        scrub_sql(order, "anonymise", "email = %s", "someone@example.com")
    with pytest.raises(IntegrityError):
        scrub_sql(order, "anonymise", "customer_name = %s", "Fake Name")
    with pytest.raises(IntegrityError):
        scrub_sql(order, "anonymise", "billing_address = %s::jsonb", '{"city": "Roma"}')


def test_raw_anonymise_with_the_right_placeholders_works_on_an_old_order() -> None:
    order = place(created=OLD)
    scrub_sql(
        order,
        "anonymise",
        "email = %s, customer_name = %s, billing_address = %s::jsonb,"
        " privacy_status = 'anonymized', anonymised_at = now()",
        engine.ANONYMISED_EMAIL,
        engine.ANONYMISED,
        '{"anonymised": true}',
    )
    order.refresh_from_db()
    assert order.email == engine.ANONYMISED_EMAIL


def test_raw_restrict_on_a_young_order_fails_and_on_an_old_one_works() -> None:
    young = place()
    with pytest.raises(IntegrityError):
        scrub_sql(
            young, "restrict", "privacy_status = 'restricted_tax_only', restricted_at = now()"
        )
    old = place(created=MID)
    scrub_sql(old, "restrict", "privacy_status = 'restricted_tax_only', restricted_at = now()")
    old.refresh_from_db()
    assert old.restricted_at is not None


# --- MIG.4: a legacy order keeps its original date ----------------------------------------------


def test_a_legacy_order_keeps_its_original_created_at() -> None:
    original = dt.datetime(2014, 5, 6, 9, 0, tzinfo=dt.UTC)
    order = place(state="closed", legacy_wp_order_id=777, placed_at=original)
    assert Order.objects.get(pk=order.pk).created_at == original
    # and its retention clock runs from it: year 10 ended on 31 Dec 2024
    services.anonymise_order(order, ctx=CTX)


def test_a_non_legacy_order_cannot_choose_its_date() -> None:
    with pytest.raises(ValueError, match="legacy"):
        place(placed_at=dt.datetime(2019, 5, 6, tzinfo=dt.UTC))


def test_the_save_guard_and_the_trigger_ignore_a_chosen_date_for_a_new_order() -> None:
    old = dt.datetime(2019, 5, 6, tzinfo=dt.UTC)
    order = OrderFactory.build(created_at=old)
    order.save()
    assert Order.objects.get(pk=order.pk).created_at > ago(minutes=5)
    kwargs = {"number": "2998-000201", "email": "a@example.com", "billing_country": "IT",
              "shipping_country": "IT", "goods_net": 0, "shipping_net": 0, "tax_total": 0,
              "grand_total": 0}  # fmt: skip
    Order.objects.bulk_create([Order(**kwargs, created_at=old)])
    assert Order.objects.get(number="2998-000201").created_at > ago(minutes=5)
    Order.objects.bulk_create(
        [Order(**{**kwargs, "number": "2998-000202"}, legacy_wp_order_id=8, created_at=old)]
    )
    assert Order.objects.get(number="2998-000202").created_at == old


def test_the_legacy_id_cannot_be_set_or_changed_after_insert() -> None:
    order = OrderFactory.create()
    with pytest.raises(IntegrityError):
        run_sql(("UPDATE orders_order SET legacy_wp_order_id = 777 WHERE id = %s", [order.pk]))
    legacy = place(state="closed", legacy_wp_order_id=778)
    with pytest.raises(IntegrityError):
        run_sql(("UPDATE orders_order SET legacy_wp_order_id = NULL WHERE id = %s", [legacy.pk]))


# --- D-30: tax_retention_until, privacy_status, unpaid orders, VIES name -----------------------


def test_tax_retention_until_is_31_december_of_year_ten_in_rome_and_immutable() -> None:
    order = place()
    year = timezone.now().astimezone(zoneinfo.ZoneInfo("Europe/Rome")).year
    assert order.tax_retention_until == dt.date(year + 10, 12, 31)
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(tax_retention_until=dt.date(2030, 1, 1))


def test_a_legacy_order_counts_the_retention_from_its_original_date() -> None:
    order = place(
        state="closed", legacy_wp_order_id=900, placed_at=dt.datetime(2014, 5, 6, tzinfo=dt.UTC)
    )
    assert order.tax_retention_until == dt.date(2024, 12, 31)
    # a new year in Rome: 23:30 UTC on 31 Dec belongs to the next year
    late = place(
        state="closed",
        legacy_wp_order_id=901,
        placed_at=dt.datetime(2014, 12, 31, 23, 30, tzinfo=dt.UTC),
    )
    assert late.tax_retention_until == dt.date(2025, 12, 31)


def test_a_new_order_starts_active() -> None:
    assert place().privacy_status == "active"


def test_privacy_status_moves_forward_only() -> None:
    order = place(created=OLD)
    services.restrict_order(order, ctx=CTX)
    order.refresh_from_db()
    assert order.privacy_status == "restricted_tax_only"
    for back in ("active",):
        with pytest.raises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(privacy_status=back)
    services.anonymise_order(order, ctx=CTX)
    order.refresh_from_db()
    assert order.privacy_status == "anonymized"
    for back in ("active", "restricted_tax_only"):
        with pytest.raises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(privacy_status=back)


def test_privacy_status_cannot_change_without_the_scrub_service() -> None:
    order = place(created=OLD)
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(privacy_status="restricted_tax_only")
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(privacy_status="anonymized")


def test_restricting_for_an_account_erasure_needs_no_waiting() -> None:
    order = place()
    with_phone(order)
    with pytest.raises(ScrubNotDue):
        services.restrict_order(order, ctx=CTX)
    services.restrict_order(order, ctx=CTX, reason="account_erasure")
    order.refresh_from_db()
    assert order.privacy_status == "restricted_tax_only"
    assert order.vendor_orders.get().carrier_contact_phone == ""
    assert order.email == "buyer@example.com"  # still in the snapshot, masked by the UI later


def test_an_order_never_paid_is_anonymised_at_once() -> None:
    order = place()  # checkout: nothing was ever paid
    services.anonymise_order(order, ctx=CTX)
    order.refresh_from_db()
    assert (order.privacy_status, order.email) == ("anonymized", engine.ANONYMISED_EMAIL)
    assert order.grand_total == Decimal("12.20")


def test_a_paid_order_is_not_anonymised_early() -> None:
    order = place()
    for state in ("awaiting_payment", "paid"):
        services.transition_order(order, state, ctx=CTX)
    with pytest.raises(ScrubNotDue):
        services.anonymise_order(order, ctx=CTX)
    with pytest.raises(IntegrityError):
        scrub_sql(
            order,
            "anonymise",
            "email = %s, privacy_status = 'anonymized', anonymised_at = now()",
            engine.ANONYMISED_EMAIL,
        )


def test_raw_sql_anonymises_an_unpaid_order_but_only_to_placeholders() -> None:
    order = place()
    with pytest.raises(IntegrityError):
        scrub_sql(order, "anonymise", "email = %s, privacy_status = 'anonymized'", "x@example.com")
    scrub_sql(
        order,
        "anonymise",
        "email = %s, privacy_status = 'anonymized', anonymised_at = now()",
        engine.ANONYMISED_EMAIL,
    )


def test_the_vies_log_keeps_the_name_returned_by_vies() -> None:
    order = place()
    assert order.vat_validation["name"] == "ROSSI SRL"
    with pytest.raises(ValueError, match="vat_validation"):
        place(vat_validation={"consultation": "X", "at": "2027-03-15T10:00:00Z"})
    place(vat_validation={})  # no VAT number checked


def test_deleting_the_user_keeps_the_order_snapshot() -> None:
    user = UserFactory.create()
    order = place(customer=user)
    user.delete()
    order.refresh_from_db()
    assert order.customer is None
    assert order.email == "buyer@example.com"

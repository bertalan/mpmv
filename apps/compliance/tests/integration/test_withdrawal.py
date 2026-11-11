"""The online withdrawal function (CMP.6 2/2): finding the contract, the statement, the
acknowledgement on a durable medium, and the audit trail."""

import datetime as dt
import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.compliance import withdrawal
from apps.compliance.models import WithdrawalStatement
from apps.core import services as core_services
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.orders.models import Order, PrivacyStatus, VendorOrder
from apps.orders.tests.integration.test_services import line_input, pay, place, vendor_input
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

BUYER = AuditContext(actor_label="anonymous", source=AuditLog.Source.SYSTEM)


def paid_order(**overrides: object) -> Order:
    a = vendor_input(VendorFactory.create(), lines=[line_input(title="Cup from A")])
    b = vendor_input(VendorFactory.create(), lines=[line_input(title="Bowl from B", sku="B-1")])
    order = place(vendor_orders=[a, b], **overrides)
    pay(order)
    order.refresh_from_db()
    return order


def sub_of(order: Order, index: int = 0) -> VendorOrder:
    return order.vendor_orders.order_by("id")[index]


# --- Finding the contract: order number and the email on the order, nothing else.


def test_the_order_is_found_by_number_and_email() -> None:
    order = paid_order(email="Buyer@Example.com")
    assert withdrawal.find_order(order.number, "buyer@example.com") == order
    assert withdrawal.find_order(f"  {order.number} ", "BUYER@example.COM ") == order


def test_a_wrong_email_or_number_finds_nothing() -> None:
    order = paid_order()
    assert withdrawal.find_order(order.number, "other@example.com") is None
    assert withdrawal.find_order("2999-999999", order.email) is None
    assert withdrawal.find_order("", "") is None


def test_an_order_that_is_no_longer_active_is_not_found() -> None:
    order = paid_order()
    Order.objects.filter(pk=order.pk).update(privacy_status=PrivacyStatus.RESTRICTED_TAX_ONLY)
    assert withdrawal.find_order(order.number, order.email) is None


# --- What the consumer may withdraw from, per vendor order.


def test_each_vendor_order_is_offered_with_its_own_window() -> None:
    order = paid_order()
    delivered = timezone.now() - dt.timedelta(days=3)
    VendorOrder.objects.filter(pk=sub_of(order, 1).pk).update(delivered_at=delivered)
    first, second = withdrawal.options(order)
    assert first.window.is_open
    assert first.window.closes_at is None
    assert second.window.is_open
    assert second.window.closes_at is not None
    assert (first.withdrawn, second.withdrawn) == (False, False)


def test_a_vendor_order_delivered_long_ago_is_closed() -> None:
    order = paid_order()
    old = timezone.now() - dt.timedelta(days=40)
    VendorOrder.objects.filter(pk=sub_of(order).pk).update(delivered_at=old)
    first, second = withdrawal.options(order)
    assert not first.window.is_open
    assert first.window.reason == "expired"
    assert second.window.is_open


def test_a_business_order_has_no_withdrawal() -> None:
    order = paid_order(customer_tax_status="b2b")
    assert not any(o.window.is_open for o in withdrawal.options(order))


def test_an_unpaid_order_has_no_withdrawal() -> None:
    a = vendor_input(VendorFactory.create())
    order = place(vendor_orders=[a])
    (only,) = withdrawal.options(order)
    assert not only.window.is_open
    assert only.window.reason == "not_paid"


def test_the_period_setting_extends_the_window(settings: object) -> None:
    order = paid_order()
    old = timezone.now() - dt.timedelta(days=20)
    VendorOrder.objects.filter(pk=sub_of(order).pk).update(delivered_at=old)
    assert not withdrawal.options(order)[0].window.is_open
    settings.WITHDRAWAL_PERIOD_DAYS = 30  # type: ignore[attr-defined]
    assert withdrawal.options(order)[0].window.is_open


# --- The statement.


def test_a_statement_targets_the_chosen_vendor_orders_only() -> None:
    order = paid_order()
    chosen = sub_of(order, 1)
    changes = withdrawal.submit_withdrawal(order, [chosen.public_id], ctx=BUYER)
    assert [c.model for c in changes.changes] == ["compliance.withdrawalstatement"]
    statement = WithdrawalStatement.objects.get()
    assert statement.vendor_order == chosen
    assert statement.order == order
    assert statement.language == order.language
    assert abs(statement.submitted_at - timezone.now()) < dt.timedelta(minutes=1)
    assert changes.changes[0].key == str(statement.public_id)


def test_dry_run_returns_the_change_set_and_writes_nothing() -> None:
    order = paid_order()
    changes = withdrawal.submit_withdrawal(
        order, [sub_of(order).public_id], ctx=BUYER, dry_run=True
    )
    assert not changes.is_empty
    assert not WithdrawalStatement.objects.exists()
    assert not OutboxEmail.objects.filter(template_key__startswith="compliance.").exists()
    assert not AuditLog.objects.filter(action=withdrawal.WITHDRAWAL_SUBMITTED).exists()


def test_the_statement_is_audited_without_the_buyers_email() -> None:
    order = paid_order()
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    entry = AuditLog.objects.get(action=withdrawal.WITHDRAWAL_SUBMITTED)
    assert entry.target_model == "compliance.withdrawalstatement"
    assert order.email not in str(entry.after) + str(entry.before) + entry.actor_label
    assert order.number in str(entry.after)


def test_nothing_chosen_or_a_foreign_vendor_order_is_refused() -> None:
    order = paid_order()
    other = paid_order()
    with pytest.raises(withdrawal.WithdrawalError) as empty:
        withdrawal.submit_withdrawal(order, [], ctx=BUYER)
    assert empty.value.code == "nothing_selected"
    with pytest.raises(withdrawal.WithdrawalError) as foreign:
        withdrawal.submit_withdrawal(order, [sub_of(other).public_id], ctx=BUYER)
    assert foreign.value.code == "unknown_vendor_order"
    with pytest.raises(withdrawal.WithdrawalError):
        withdrawal.submit_withdrawal(order, [uuid.uuid4()], ctx=BUYER)
    assert not WithdrawalStatement.objects.exists()


def test_a_closed_window_is_refused_and_nothing_is_written() -> None:
    order = paid_order()
    old = timezone.now() - dt.timedelta(days=40)
    VendorOrder.objects.filter(pk=sub_of(order).pk).update(delivered_at=old)
    with pytest.raises(withdrawal.WithdrawalError) as closed:
        withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    assert closed.value.code == "not_available"
    assert not WithdrawalStatement.objects.exists()


def test_a_second_statement_for_the_same_vendor_order_is_refused() -> None:
    order = paid_order()
    target = sub_of(order).public_id
    withdrawal.submit_withdrawal(order, [target], ctx=BUYER)
    with pytest.raises(withdrawal.WithdrawalError) as again:
        withdrawal.submit_withdrawal(order, [target], ctx=BUYER)
    assert again.value.code == "already_withdrawn"
    assert WithdrawalStatement.objects.count() == 1
    assert withdrawal.options(order)[0].withdrawn


def test_the_database_allows_one_statement_per_vendor_order() -> None:
    order = paid_order()
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    first = WithdrawalStatement.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        WithdrawalStatement.objects.create(
            order=order,
            vendor_order=first.vendor_order,
            submitted_at=timezone.now(),
            language="en",
        )


def test_a_statement_is_append_only() -> None:
    order = paid_order()
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    statement = WithdrawalStatement.objects.get()
    statement.language = "de"
    with pytest.raises(PermissionError):
        statement.save()
    with pytest.raises(PermissionError):
        statement.delete()


# --- The acknowledgement on a durable medium, and the vendor's notice.


def test_the_buyer_gets_one_acknowledgement_with_content_date_and_time() -> None:
    order = paid_order(language="en")
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    mail = OutboxEmail.objects.get(template_key=withdrawal.ACK_TEMPLATE)
    assert mail.recipient == order.email
    rendered = core_services.render_email(mail.template_key, mail.language, mail.context_data)
    statement = WithdrawalStatement.objects.get()
    for text in (rendered.text, rendered.html):
        assert order.number in text
        assert "Cup from A" in text or "Bowl from B" in text
        assert str(statement.public_id) in text
        assert "withdraw" in text.lower()
        assert f"{statement.submitted_at.astimezone():%Y-%m-%d}" in text
    statement.refresh_from_db()
    assert statement.acknowledged_at is not None


def test_the_acknowledgement_is_in_the_order_language() -> None:
    order = paid_order(language="it")
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    mail = OutboxEmail.objects.get(template_key=withdrawal.ACK_TEMPLATE)
    assert mail.language == "it"
    rendered = core_services.render_email(mail.template_key, mail.language, mail.context_data)
    assert "recesso" in rendered.text.lower()


def test_the_vendor_is_told_without_the_buyers_personal_data() -> None:
    order = paid_order()
    target = sub_of(order)
    withdrawal.submit_withdrawal(order, [target.public_id], ctx=BUYER)
    mail = OutboxEmail.objects.get(template_key=withdrawal.VENDOR_TEMPLATE)
    assert mail.recipient == target.vendor.contact_email
    blob = str(mail.context_data)
    assert order.email not in blob
    assert order.customer_name not in blob or not order.customer_name
    assert order.number in blob
    assert "Bowl from B" not in blob  # only this vendor's own lines, and the other one's absent


def test_one_acknowledgement_per_vendor_order_for_a_multi_choice() -> None:
    order = paid_order()
    ids = [s.public_id for s in order.vendor_orders.order_by("id")]
    withdrawal.submit_withdrawal(order, ids, ctx=BUYER)
    assert OutboxEmail.objects.filter(template_key=withdrawal.ACK_TEMPLATE).count() == 1
    assert OutboxEmail.objects.filter(template_key=withdrawal.VENDOR_TEMPLATE).count() == 2
    assert WithdrawalStatement.objects.count() == 2
    ack = OutboxEmail.objects.get(template_key=withdrawal.ACK_TEMPLATE)
    assert len(ack.context_data["statements"]) == 2


def test_the_submission_does_not_change_order_states() -> None:
    order = paid_order()
    before = [(s.pk, s.state) for s in order.vendor_orders.order_by("id")]
    withdrawal.submit_withdrawal(order, [sub_of(order).public_id], ctx=BUYER)
    order.refresh_from_db()
    assert before == [(s.pk, s.state) for s in order.vendor_orders.order_by("id")]
    assert order.state == "paid"  # the refund flow (ORD.8) moves states, not this record

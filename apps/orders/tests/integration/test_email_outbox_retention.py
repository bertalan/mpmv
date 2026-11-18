"""ORD.26: the outbox keeps the message rendered at queue time and forgets it with the order
(D-29 scrub, D-30 restriction)."""

import json

import pytest
from django.db import connection

from apps.core import mail
from apps.core import services as core_services
from apps.core.models import AuditLog, OutboxEmail, OutboxEmailEvent
from apps.orders import engine, notifications, services
from apps.orders.models import Order
from apps.orders.tests.integration.test_retention import CTX, MID, OLD, place

pytestmark = pytest.mark.django_db

SECRETS = ("Mario", "Rossi", "buyer@example.com", "Via Test", "Cup")


def queue_for(order: Order) -> OutboxEmail:
    notifications.notify_order_placed(order)
    notifications.notify_order_paid(order)
    row = OutboxEmail.objects.filter(order_ref=order.public_id).first()
    assert row is not None
    reason = "the shop called Mario Rossi"
    OutboxEmail.objects.filter(pk=row.pk).update(
        context=json.dumps({"reason": reason, "ship_to": "2 Via Test", "lines": ["Cup"]}),
        subject="Order for Mario Rossi",
        text_body="Ship to 2 Via Test: Cup\n",
        html_body="<p>buyer@example.com</p>",
    )
    return OutboxEmail.objects.get(pk=row.pk)


def test_a_queued_order_email_is_tied_to_its_order_and_stores_its_rendered_message() -> None:
    order = place(created=OLD)
    notifications.notify_order_placed(order)
    row = OutboxEmail.objects.get()
    assert row.order_ref == order.public_id
    assert order.number in row.subject
    assert row.text_body.strip()
    assert row.html_body.strip()


def test_every_order_email_producer_ties_its_rows_to_the_order() -> None:
    order = place(created=OLD)
    notifications.notify_order_placed(order)
    notifications.notify_order_paid(order)
    notifications.notify_order_cancelled(order)
    sub = order.vendor_orders.get()
    notifications.notify_vendor_order_shipped(sub)
    notifications.notify_vendor_order_delivered(sub)
    notifications.notify_vendor_order_cancelled(sub)
    rows = OutboxEmail.objects.all()
    assert rows.count() >= 4
    assert not rows.filter(order_ref__isnull=True).exists()


def test_anonymising_an_order_leaves_no_personal_data_in_its_outbox_rows() -> None:
    order = place(created=OLD)
    row = queue_for(order)
    other = OutboxEmail.objects.create(
        recipient="someone@example.com", template_key="tests.reminder", language="en"
    )
    key, language, created = row.template_key, row.language, row.created_at
    services.anonymise_order(order, ctx=CTX)
    row.refresh_from_db()
    blob = " ".join(
        (row.recipient, row.context, row.subject, row.text_body, row.html_body, row.last_error)
    )
    assert not [word for word in SECRETS if word in blob], blob
    assert (row.template_key, row.language, row.created_at) == (key, language, created)
    other.refresh_from_db()
    assert other.recipient == "someone@example.com"  # another email is untouched


def test_a_waiting_email_is_cancelled_by_the_scrub_with_a_history_entry() -> None:
    order = place(created=OLD)
    row = queue_for(order)
    assert row.status == OutboxEmail.Status.QUEUED
    services.anonymise_order(order, ctx=CTX)
    row.refresh_from_db()
    assert row.status == OutboxEmail.Status.SUPPRESSED
    assert OutboxEmailEvent.objects.filter(email=row, detail="order anonymised").exists()


def test_the_scrub_is_idempotent_and_audited_without_personal_data() -> None:
    order = place(created=OLD)
    queue_for(order)
    services.anonymise_order(order, ctx=CTX)
    events = OutboxEmailEvent.objects.count()
    audited = AuditLog.objects.count()
    changeset = core_services.scrub_order_emails(order.public_id, recipient=engine.ANONYMISED_EMAIL)
    assert changeset.is_empty
    assert OutboxEmailEvent.objects.count() == events
    assert AuditLog.objects.count() == audited
    entries = AuditLog.objects.filter(target_model="core.outboxemail")
    assert entries.exists()
    assert not [w for e in entries for w in SECRETS if w in str(e.before) + str(e.after)]


def test_a_restricted_order_keeps_its_emails_encrypted_at_rest() -> None:
    order = place(created=MID)
    row = queue_for(order)
    services.restrict_order(order, ctx=CTX)
    kept = OutboxEmail.objects.get(pk=row.pk)
    assert kept.subject == "Order for Mario Rossi"
    assert kept.recipient == order.email
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT subject, text_body, html_body, context FROM core_outboxemail WHERE id = %s",
            [row.pk],
        )
        raw = " ".join(str(column) for column in cursor.fetchone())
    assert "Mario" not in raw
    assert "Via Test" not in raw


def test_a_changed_wording_between_queueing_and_sending_does_not_change_the_mail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = place(created=OLD)
    notifications.notify_order_placed(order)
    row = OutboxEmail.objects.get()
    sender = core_services.email_sender()
    assert isinstance(sender, mail.MemoryEmailSender)
    sender.clear()
    stored = (row.subject, row.text_body, row.html_body)
    monkeypatch.setattr(
        core_services,
        "render_email",
        lambda *a, **k: mail.RenderedEmail("changed", "changed\n", "<p>changed</p>"),
    )
    core_services.deliver_email(row.public_id)
    out = sender.sent[-1]
    assert (out.subject, out.text_body, out.html_body) == stored


def test_the_scrub_dry_run_reports_the_change_and_writes_nothing() -> None:
    order = place(created=OLD)
    row = queue_for(order)
    before = OutboxEmail.objects.get(pk=row.pk)
    changeset = core_services.scrub_order_emails(
        order.public_id, recipient=engine.ANONYMISED_EMAIL, dry_run=True
    )
    assert not changeset.is_empty
    after = OutboxEmail.objects.get(pk=row.pk)
    assert (after.status, after.recipient, after.subject) == (
        before.status,
        before.recipient,
        before.subject,
    )
    assert not OutboxEmailEvent.objects.filter(email=row, detail="order anonymised").exists()

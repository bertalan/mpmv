"""Transactional email outbox on the real queue (ARC.16).

Done when: OTP, reminder and order emails are sent through the outbox; a transient failure
retries and a permanent one stops; a hard bounce suppresses later sends; no email contains a
tracking pixel. The templates under `apps/core/tests/templates/email/tests/` stand in for the
real ones that the feature tasks add (CORE.15, CMP.10, ORD.9).
"""

import datetime as dt
import json
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core import mail, services
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, EmailSuppression, OutboxEmail, OutboxEmailEvent
from apps.core.tasks import list_jobs, run_worker
from apps.core.tests.factories import (
    EmailSuppressionFactory,
    OutboxEmailEventFactory,
    OutboxEmailFactory,
)

ROOT = Path(settings.BASE_DIR)
QUEUE_TABLES = (
    "procrastinate_jobs, procrastinate_periodic_defers, procrastinate_events, procrastinate_workers"
)
CTX = AuditContext(actor_label="webhook:email", source=AuditLog.Source.SYSTEM)

OTP = "tests.one_time_code"
REMINDER = "tests.reminder"
ORDER = "tests.order_confirmation"


@pytest.fixture
def sender() -> Iterator[mail.MemoryEmailSender]:
    backend = services.email_sender()
    assert isinstance(backend, mail.MemoryEmailSender)
    backend.clear()
    yield backend
    backend.clear()


def _purge_queue() -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {QUEUE_TABLES} RESTART IDENTITY CASCADE")


@pytest.fixture
def empty_queue() -> Iterator[None]:
    _purge_queue()
    yield
    _purge_queue()


def _drain() -> None:
    run_worker(wait=False, scheduler=False)


def _make_due() -> None:
    """Stand in for the passing of time: the retries the queue scheduled are due now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE procrastinate_jobs SET scheduled_at = now() - interval '1 second' "
            "WHERE status = 'todo'"
        )


def _enqueue(template_key: str = OTP, **kwargs: Any) -> OutboxEmail:
    kwargs.setdefault("recipient", "customer@example.com")
    kwargs.setdefault("language", "en")
    kwargs.setdefault("context", {"code": "482913", "minutes": 10})
    changeset = services.enqueue_email(template_key=template_key, **kwargs)
    return OutboxEmail.objects.get(public_id=changeset.changes[0].key)


def _history(email: OutboxEmail) -> list[str]:
    return list(email.events.order_by("pk").values_list("status", flat=True))


# --- Enqueueing -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_enqueue_dry_run_returns_the_change_and_writes_nothing() -> None:
    changeset = services.enqueue_email(
        recipient="customer@example.com",
        template_key=OTP,
        language="it",
        context={"code": "482913", "minutes": 10},
        dry_run=True,
    )
    assert changeset.summary() == {"create": 1, "update": 0, "delete": 0}
    after = changeset.changes[0].after
    assert after is not None
    assert after["template_key"] == OTP
    assert after["status"] == OutboxEmail.Status.QUEUED
    assert "context" not in after  # one-time codes never reach a change report
    assert not OutboxEmail.objects.exists()


@pytest.mark.django_db
def test_enqueue_stores_a_snapshot_queues_the_job_and_records_the_history() -> None:
    email = _enqueue(language="it")
    assert email.status == OutboxEmail.Status.QUEUED
    assert email.language == "it"
    assert email.context_data == {"code": "482913", "minutes": 10}
    assert email.attempts == 0
    assert _history(email) == [OutboxEmail.Status.QUEUED]
    assert [job.status for job in list_jobs("core.send_email")] == ["todo"]


@pytest.mark.django_db
def test_the_context_snapshot_is_encrypted_at_rest() -> None:
    email = _enqueue()
    with connection.cursor() as cursor:
        cursor.execute("SELECT context FROM core_outboxemail WHERE id = %s", [email.pk])
        (raw,) = cursor.fetchone()
    assert "482913" not in raw
    assert raw.startswith("enc1:")


@pytest.mark.django_db
def test_an_unknown_template_is_refused_at_enqueue() -> None:
    with pytest.raises(ValueError, match=r"tests\.missing"):
        services.enqueue_email(
            recipient="customer@example.com",
            template_key="tests.missing",
            language="en",
            context={},
        )
    assert not OutboxEmail.objects.exists()


@pytest.mark.django_db
def test_an_unknown_language_falls_back_to_english() -> None:
    email = _enqueue(language="xx")
    assert email.language == "en"


@pytest.mark.django_db
def test_the_same_idempotency_key_enqueues_one_email_only() -> None:
    first = _enqueue(ORDER, context=_order_context(), idempotency_key="order:A-1001:confirmation")
    again = services.enqueue_email(
        recipient="customer@example.com",
        template_key=ORDER,
        language="en",
        context=_order_context(),
        idempotency_key="order:A-1001:confirmation",
    )
    assert again.is_empty
    assert OutboxEmail.objects.get() == first
    assert len(list_jobs("core.send_email")) == 1


# --- Rendering ------------------------------------------------------------------------------


def _order_context() -> dict[str, Any]:
    return {"order_number": "A-1001", "customer_name": "Ada", "total": "42.50"}


@pytest.mark.django_db
def test_a_per_language_template_wins_and_missing_ones_fall_back_to_english() -> None:
    context = {"item": "the GPSR checklist", "due": "2026-12-01", "action_url": "https://x.test/c"}
    italian = services.render_email(REMINDER, "it", context)
    german = services.render_email(REMINDER, "de", context)
    assert italian.subject == "Promemoria: the GPSR checklist"
    assert german.subject == "Reminder: the GPSR checklist"  # no German text: EN, as spec 09
    assert "Please complete the GPSR checklist by 2026-12-01." in german.text_body
    assert 'lang="de"' in german.html_body


@pytest.mark.django_db
def test_the_text_body_is_not_html_escaped_but_the_html_body_is() -> None:
    rendered = services.render_email(ORDER, "en", {**_order_context(), "customer_name": "A & B"})
    assert "Thank you, A & B." in rendered.text_body
    assert "Thank you, A &amp; B." in rendered.html_body
    assert "\n" not in rendered.subject


# --- Sending through the worker -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_otp_reminder_and_order_emails_go_through_the_outbox(
    sender: mail.MemoryEmailSender,
) -> None:
    otp = _enqueue(OTP, recipient="user@example.com")
    reminder = _enqueue(
        REMINDER,
        recipient="vendor@example.com",
        language="it",
        context={"item": "GPSR", "due": "2026-12-01", "action_url": "https://x.test/c"},
    )
    order = _enqueue(ORDER, recipient="customer@example.com", context=_order_context())
    assert sender.sent == []  # nothing leaves before the worker runs

    _drain()

    by_recipient = {email.recipient: email for email in sender.sent}
    assert "482913" in by_recipient["user@example.com"].text_body
    assert by_recipient["vendor@example.com"].subject == "Promemoria: GPSR"
    assert by_recipient["customer@example.com"].subject == "Order A-1001 confirmed"
    for email in (otp, reminder, order):
        email.refresh_from_db()
        assert email.status == OutboxEmail.Status.SENT
        assert email.attempts == 1
        assert email.sent_at is not None
        assert email.provider_message_id
        assert _history(email) == [OutboxEmail.Status.QUEUED, OutboxEmail.Status.SENT]
    assert sorted(sent.idempotency_key for sent in sender.sent) == sorted(
        str(email.public_id) for email in (otp, reminder, order)
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_transient_failure_is_retried_with_backoff_then_sent(
    sender: mail.MemoryEmailSender,
) -> None:
    sender.fail_next(mail.TransientSendError("provider timeout"))
    before = timezone.now()
    email = _enqueue()

    _drain()

    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.RETRYING
    assert email.attempts == 1
    assert "provider timeout" in email.last_error
    assert email.next_attempt_at is not None
    assert email.next_attempt_at >= before + dt.timedelta(seconds=mail.BASE_DELAY)
    (job,) = list_jobs("core.send_email")
    assert job.status == "todo"  # the queue retries it later
    assert job.scheduled_at is not None
    assert job.scheduled_at >= before + dt.timedelta(seconds=mail.BASE_DELAY)
    assert sender.sent == []

    _make_due()
    _drain()

    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.SENT
    assert email.attempts == 2
    assert len(sender.sent) == 1
    assert _history(email) == [
        OutboxEmail.Status.QUEUED,
        OutboxEmail.Status.RETRYING,
        OutboxEmail.Status.SENT,
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("empty_queue")
def test_a_permanent_failure_stops_at_once(sender: mail.MemoryEmailSender) -> None:
    sender.fail_next(mail.PermanentSendError("mailbox does not exist"))
    email = _enqueue()

    _drain()
    _make_due()
    _drain()

    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.FAILED
    assert email.attempts == 1
    assert "mailbox does not exist" in email.last_error
    assert sender.attempts == 1
    assert [job.status for job in list_jobs("core.send_email")] == ["succeeded"]


@pytest.mark.django_db
def test_transient_failures_stop_after_the_last_retry(sender: mail.MemoryEmailSender) -> None:
    email = _enqueue()
    sender.fail_next(*(mail.TransientSendError("busy") for _ in range(mail.MAX_RETRIES + 1)))
    for _ in range(mail.MAX_RETRIES):
        with pytest.raises(services.EmailRetryScheduled):
            services.deliver_email(email.public_id)

    services.deliver_email(email.public_id)  # the last attempt gives up without raising

    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.FAILED
    assert email.attempts == mail.MAX_RETRIES + 1
    assert email.next_attempt_at is None
    assert sender.sent == []


@pytest.mark.django_db
def test_an_email_is_never_sent_twice(sender: mail.MemoryEmailSender) -> None:
    email = _enqueue()
    services.deliver_email(email.public_id)
    replay = services.deliver_email(email.public_id)  # a retried or replayed job
    assert replay.is_empty
    assert len(sender.sent) == 1
    email.refresh_from_db()
    assert email.attempts == 1


@pytest.mark.django_db
def test_deliver_dry_run_sends_nothing(sender: mail.MemoryEmailSender) -> None:
    email = _enqueue()
    changeset = services.deliver_email(email.public_id, dry_run=True)
    assert changeset.changes[0].fields["status"] == (
        OutboxEmail.Status.QUEUED,
        OutboxEmail.Status.SENT,
    )
    assert sender.attempts == 0
    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.QUEUED


# --- Bounces, complaints and suppression ----------------------------------------------------


@pytest.mark.django_db
def test_a_hard_bounce_suppresses_later_sends(sender: mail.MemoryEmailSender) -> None:
    first = _enqueue(recipient="Gone@Example.com")
    services.deliver_email(first.public_id)
    first.refresh_from_db()

    changeset = services.record_email_feedback(
        mail.FeedbackEvent(
            mail.HARD_BOUNCE, "gone@example.com", first.provider_message_id, "5.1.1"
        ),
        ctx=CTX,
    )

    assert changeset.summary()["create"] == 1
    suppression = EmailSuppression.objects.get()
    assert suppression.address == "gone@example.com"
    assert suppression.reason == EmailSuppression.Reason.HARD_BOUNCE
    assert _history(first)[-1] == OutboxEmailEvent.Kind.BOUNCED
    entry = AuditLog.objects.get(action="core.email.suppressed")
    assert entry.target_key == str(suppression.public_id)

    later = _enqueue(recipient="gone@example.com")
    assert later.status == OutboxEmail.Status.SUPPRESSED
    services.deliver_email(later.public_id)
    assert len(sender.sent) == 1  # only the email sent before the bounce


@pytest.mark.django_db
def test_a_bounce_arriving_before_the_send_stops_the_queued_email(
    sender: mail.MemoryEmailSender,
) -> None:
    email = _enqueue(recipient="complains@example.com")
    services.record_email_feedback(
        mail.FeedbackEvent(mail.COMPLAINT, "complains@example.com", "", "abuse"), ctx=CTX
    )
    services.deliver_email(email.public_id)
    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.SUPPRESSED
    assert sender.attempts == 0
    assert EmailSuppression.objects.get().reason == EmailSuppression.Reason.COMPLAINT


@pytest.mark.django_db
def test_a_soft_bounce_is_recorded_but_does_not_suppress(sender: mail.MemoryEmailSender) -> None:
    email = _enqueue()
    services.deliver_email(email.public_id)
    email.refresh_from_db()
    changeset = services.record_email_feedback(
        mail.FeedbackEvent(mail.SOFT_BOUNCE, email.recipient, email.provider_message_id, "full"),
        ctx=CTX,
    )
    assert changeset.is_empty
    assert not EmailSuppression.objects.exists()
    assert _history(email)[-1] == OutboxEmailEvent.Kind.SOFT_BOUNCED


@pytest.mark.django_db
def test_feedback_dry_run_and_repeated_bounces_change_nothing_more() -> None:
    EmailSuppressionFactory.create(address="gone@example.com")
    preview = services.record_email_feedback(
        mail.FeedbackEvent(mail.HARD_BOUNCE, "new@example.com", "", ""), ctx=CTX, dry_run=True
    )
    assert preview.summary()["create"] == 1
    repeated = services.record_email_feedback(
        mail.FeedbackEvent(mail.HARD_BOUNCE, "gone@example.com", "", ""), ctx=CTX
    )
    assert repeated.is_empty
    assert list(EmailSuppression.objects.values_list("address", flat=True)) == ["gone@example.com"]
    assert services.is_suppressed("GONE@example.com")
    assert not services.is_suppressed("new@example.com")


# --- The signed webhook stub ----------------------------------------------------------------


@pytest.fixture
def webhook_secret() -> Iterator[str]:
    secret = secrets.token_urlsafe(32)
    with override_settings(EMAIL_WEBHOOK_SECRET=secret):
        yield secret


def _post(client: Client, body: bytes, headers: dict[str, str]) -> Any:
    return client.post(
        reverse("email_webhook"), data=body, content_type="application/json", headers=headers
    )


@pytest.mark.django_db
def test_the_webhook_records_a_signed_hard_bounce(client: Client, webhook_secret: str) -> None:
    body = json.dumps(
        {"events": [{"type": "hard_bounce", "recipient": "gone@example.com", "message_id": "x"}]}
    ).encode()
    headers = mail.SignedJsonFeedback(webhook_secret).sign(
        body, timestamp=int(timezone.now().timestamp())
    )
    response = _post(client, body, headers)
    assert response.status_code == 204
    assert services.is_suppressed("gone@example.com")
    assert AuditLog.objects.get(action="core.email.suppressed").actor_label == "webhook:email"


@pytest.mark.django_db
def test_the_webhook_refuses_a_bad_signature_and_malformed_payloads(
    client: Client, webhook_secret: str
) -> None:
    body = json.dumps({"events": [{"type": "complaint", "recipient": "a@example.com"}]}).encode()
    forged = mail.SignedJsonFeedback(secrets.token_urlsafe(32)).sign(
        body, timestamp=int(timezone.now().timestamp())
    )
    assert _post(client, body, forged).status_code == 403
    junk = b"not json"
    signed = mail.SignedJsonFeedback(webhook_secret).sign(
        junk, timestamp=int(timezone.now().timestamp())
    )
    assert _post(client, junk, signed).status_code == 400
    assert client.get(reverse("email_webhook")).status_code == 405
    assert not EmailSuppression.objects.exists()


@pytest.mark.django_db
@override_settings(EMAIL_WEBHOOK_SECRET="")
def test_the_webhook_is_off_without_a_secret(client: Client) -> None:
    assert _post(client, b"{}", {}).status_code == 404


# --- No tracking pixels, nothing the CSP would block ----------------------------------------


def _shipped_html_templates() -> list[Path]:
    roots = [*ROOT.glob("apps/*/templates"), *ROOT.glob("apps/*/tests/templates"), ROOT / "themes"]
    found = [path for root in roots for path in root.glob("**/email/**/body.html")]
    return [path for path in found if "email/tests/unsafe" not in path.as_posix()]


def test_no_email_template_contains_a_tracking_pixel_or_inline_code() -> None:
    templates = _shipped_html_templates()
    assert templates  # the scan finds the templates
    offenders = {
        str(path.relative_to(ROOT)): mail.html_violations(path.read_text())
        for path in templates
        if mail.html_violations(path.read_text())
    }
    assert offenders == {}


@pytest.mark.django_db
def test_no_sent_email_contains_a_tracking_pixel(sender: mail.MemoryEmailSender) -> None:
    for key, context in (
        (OTP, {"code": "1", "minutes": 5}),
        (REMINDER, {"item": "x", "due": "y", "action_url": "https://x.test/"}),
        (ORDER, _order_context()),
    ):
        services.deliver_email(_enqueue(key, context=context).public_id)
    assert len(sender.sent) == 3
    for email in sender.sent:
        assert mail.html_violations(email.html_body) == []
        assert "<img" not in email.html_body.lower()


@pytest.mark.django_db
def test_an_email_with_a_tracking_pixel_is_never_sent(sender: mail.MemoryEmailSender) -> None:
    email = _enqueue("tests.unsafe", context={})
    services.deliver_email(email.public_id)
    email.refresh_from_db()
    assert email.status == OutboxEmail.Status.FAILED
    assert "img" in email.last_error
    assert sender.attempts == 0


# --- Factories ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_outbox_history_is_append_only() -> None:
    event = OutboxEmailEventFactory.create(email=OutboxEmailFactory.create())
    event.detail = "changed"
    with pytest.raises(PermissionError):
        event.save()
    with pytest.raises(PermissionError):
        event.delete()

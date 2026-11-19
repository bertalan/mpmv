"""ORD.26: the outbox sends the message rendered when it was queued."""

from collections.abc import Iterator

import pytest

from apps.core import mail, services
from apps.core.models import EmailSuppression, OutboxEmail

pytestmark = pytest.mark.django_db


@pytest.fixture
def sender() -> Iterator[mail.MemoryEmailSender]:
    backend = services.email_sender()
    assert isinstance(backend, mail.MemoryEmailSender)
    backend.clear()
    yield backend
    backend.clear()


def _queue() -> OutboxEmail:
    services.enqueue_email(
        recipient="user@example.com",
        template_key="tests.one_time_code",
        language="en",
        context={"code": "482913"},
        idempotency_key="k:1",
    )
    return OutboxEmail.objects.get()


def test_the_rendered_message_is_stored_when_queued(sender: mail.MemoryEmailSender) -> None:
    row = _queue()
    assert "482913" in row.text_body
    assert row.subject
    assert row.html_body


def test_a_retry_and_a_resend_use_the_stored_message_not_a_new_render(
    sender: mail.MemoryEmailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _queue()
    stored = (row.subject, row.text_body, row.html_body)
    monkeypatch.setattr(
        services, "render_email", lambda *a, **k: mail.RenderedEmail("new", "new\n", "<p>new</p>")
    )
    sender.fail_next(mail.TransientSendError("timeout"))
    with pytest.raises(services.EmailRetryScheduled):
        services.deliver_email(row.public_id)
    services.deliver_email(row.public_id)
    assert [(e.subject, e.text_body, e.html_body) for e in sender.sent] == [stored]


def test_a_row_queued_before_ord26_still_renders_when_sent(
    sender: mail.MemoryEmailSender,
) -> None:
    row = _queue()
    OutboxEmail.objects.filter(pk=row.pk).update(subject="", text_body="", html_body="")
    services.deliver_email(row.public_id)
    assert "482913" in sender.sent[0].text_body


def test_a_suppressed_address_stores_no_message() -> None:
    EmailSuppression.objects.create(address="user@example.com", reason="hard_bounce")
    row = _queue()
    assert (row.status, row.subject, row.text_body, row.html_body) == ("suppressed", "", "", "")

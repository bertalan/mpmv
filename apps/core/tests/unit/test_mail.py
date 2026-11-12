"""Transactional email building blocks without a database (ARC.16).

The sender protocol and its test and console backends, the retry schedule shared with the task
queue, the HTML rules (no tracking pixel, nothing the CSP would block) and the provider-neutral
signed feedback format.
"""

import io
import json
import secrets

import pytest
from django.core import checks
from django.test import override_settings

from apps.core import mail
from apps.core.checks import check_email_settings
from apps.core.tasks import Retry


def _email(n: int = 1) -> mail.OutgoingEmail:
    return mail.OutgoingEmail(
        recipient=f"customer{n}@example.com",
        from_address="no-reply@example.com",
        subject="Subject",
        text_body="Text",
        html_body="<p>Text</p>",
        idempotency_key=f"key-{n}",
    )


# --- Backends -------------------------------------------------------------------------------


def test_memory_sender_keeps_what_it_sends_with_a_provider_id() -> None:
    sender = mail.MemoryEmailSender()
    first = sender.send(_email(1))
    second = sender.send(_email(2))
    assert [email.recipient for email in sender.sent] == [
        "customer1@example.com",
        "customer2@example.com",
    ]
    assert first.provider_message_id != second.provider_message_id
    sender.clear()
    assert sender.sent == []


def test_an_email_has_no_attachment_unless_given_one() -> None:
    assert _email().attachments == ()
    attachment = mail.Attachment(filename="a.pdf", content_type="application/pdf", content=b"%PDF")
    with_file = mail.OutgoingEmail(**{**_email().__dict__, "attachments": (attachment,)})
    assert with_file.attachments == (attachment,)


def test_console_sender_lists_attachments_without_printing_their_bytes() -> None:
    stream = io.StringIO()
    attachment = mail.Attachment(filename="a.pdf", content_type="application/pdf", content=b"%PDF")
    email = mail.OutgoingEmail(**{**_email().__dict__, "attachments": (attachment,)})
    mail.ConsoleEmailSender(stream).send(email)
    assert "a.pdf" in stream.getvalue()
    assert "%PDF" not in stream.getvalue()


def test_memory_sender_raises_the_scripted_failures_in_order_then_sends() -> None:
    sender = mail.MemoryEmailSender()
    sender.fail_next(mail.TransientSendError("timeout"), mail.PermanentSendError("rejected"))
    with pytest.raises(mail.TransientSendError):
        sender.send(_email())
    with pytest.raises(mail.PermanentSendError):
        sender.send(_email())
    sender.send(_email())
    assert len(sender.sent) == 1
    assert sender.attempts == 3


def test_console_sender_prints_the_message() -> None:
    stream = io.StringIO()
    result = mail.ConsoleEmailSender(stream=stream).send(_email())
    output = stream.getvalue()
    assert "To: customer1@example.com" in output
    assert "Subject: Subject" in output
    assert "Text" in output
    assert result.provider_message_id.startswith("console-")


def test_both_backends_satisfy_the_sender_protocol() -> None:
    assert isinstance(mail.MemoryEmailSender(), mail.EmailSender)
    assert isinstance(mail.ConsoleEmailSender(), mail.EmailSender)


# --- Retries --------------------------------------------------------------------------------


def test_retry_delay_backs_off_exponentially_then_gives_up() -> None:
    delays = [mail.retry_delay(attempts) for attempts in range(1, mail.MAX_RETRIES + 2)]
    assert delays[:3] == [mail.BASE_DELAY, 2 * mail.BASE_DELAY, 4 * mail.BASE_DELAY]
    assert all(delay is not None and delay <= mail.MAX_DELAY for delay in delays[:-1])
    assert delays[-1] is None


def test_retry_delay_matches_the_task_queue_policy() -> None:
    """The outbox's next attempt is when the queue retries the job."""
    policy = Retry(
        max_attempts=mail.MAX_RETRIES, base_delay=mail.BASE_DELAY, max_delay=mail.MAX_DELAY
    )
    for attempts in range(1, mail.MAX_RETRIES + 3):
        assert mail.retry_delay(attempts) == policy.delay_after(attempts - 1)


# --- HTML rules -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        '<p>Hi</p><img src="https://tracker.example.com/o.gif" width="1" height="1">',
        "<p>Hi</p><IMG SRC=cid:logo>",
        '<table background="https://tracker.example.com/bg.png"><tr><td>x</td></tr></table>',
        '<div style="background:url(https://tracker.example.com/p.png)">x</div>',
        "<style>p { color: red }</style><p>x</p>",
        "<script>alert(1)</script>",
        '<a href="https://example.com" onclick="track()">x</a>',
        '<a href="javascript:alert(1)">x</a>',
        '<link rel="stylesheet" href="https://example.com/a.css">',
        '<iframe src="https://example.com"></iframe>',
        '<picture><source srcset="https://tracker.example.com/p.png"></picture>',
    ],
)
def test_html_with_a_remote_resource_or_inline_code_is_refused(html: str) -> None:
    assert mail.html_violations(html) != []


def test_plain_semantic_html_with_links_is_accepted() -> None:
    html = (
        '<!doctype html><html lang="it"><body><h1>Order</h1>'
        '<p>Thanks. <a href="https://example.com/orders/1">Open</a></p>'
        "<table><tr><th>Total</th><td>10.00 EUR</td></tr></table></body></html>"
    )
    assert mail.html_violations(html) == []


# --- Signed feedback (bounces and complaints) -----------------------------------------------


def _payload(*events: dict[str, str]) -> bytes:
    return json.dumps({"events": list(events)}).encode()


def test_signed_feedback_verifies_its_own_signature() -> None:
    parser = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    body = _payload({"type": "hard_bounce", "recipient": "a@example.com", "message_id": "m1"})
    headers = parser.sign(body, timestamp=1_800_000_000)
    assert parser.verify(headers, body, now=1_800_000_010)


def test_signed_feedback_refuses_another_secret_a_changed_body_or_an_old_timestamp() -> None:
    parser = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    other = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    body = _payload({"type": "complaint", "recipient": "a@example.com", "message_id": "m1"})
    headers = parser.sign(body, timestamp=1_800_000_000)
    assert not other.verify(headers, body, now=1_800_000_000)
    assert not parser.verify(headers, body + b" ", now=1_800_000_000)
    assert not parser.verify(headers, body, now=1_800_000_000 + parser.tolerance + 1)
    assert not parser.verify({}, body, now=1_800_000_000)


@pytest.mark.parametrize("stamp", ["\u00b2", "\u2460", "\u0661\u0662", "1" * 5000, " 1", "-1", ""])
def test_signed_feedback_refuses_a_timestamp_that_is_not_plain_ascii_digits(stamp: str) -> None:
    """Superscript and circled digits pass `str.isdigit` but `int()` refuses them, and a very
    long number exceeds `int()`'s digit limit: both are a refusal, never an exception."""
    parser = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    body = _payload({"type": "complaint", "recipient": "a@example.com", "message_id": "m1"})
    headers = {**parser.sign(body, timestamp=1_800_000_000), mail.TIMESTAMP_HEADER: stamp}
    assert not parser.verify(headers, body, now=1_800_000_000)


def test_signed_feedback_parses_provider_neutral_events() -> None:
    parser = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    events = parser.parse(
        _payload(
            {"type": "hard_bounce", "recipient": "A@Example.com", "message_id": "m1"},
            {"type": "complaint", "recipient": "b@example.com", "detail": "abuse"},
            {"type": "soft_bounce", "recipient": "c@example.com", "message_id": "m3"},
        )
    )
    assert events == [
        mail.FeedbackEvent(mail.HARD_BOUNCE, "a@example.com", "m1", ""),
        mail.FeedbackEvent(mail.COMPLAINT, "b@example.com", "", "abuse"),
        mail.FeedbackEvent(mail.SOFT_BOUNCE, "c@example.com", "m3", ""),
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        _payload({"type": "opened", "recipient": "a@example.com"}),
        _payload({"type": "hard_bounce"}),
    ],
)
def test_signed_feedback_refuses_malformed_payloads(body: bytes) -> None:
    parser = mail.SignedJsonFeedback(secrets.token_urlsafe(32))
    with pytest.raises(mail.InvalidFeedback):
        parser.parse(body)


def test_addresses_are_normalised_for_suppression() -> None:
    assert mail.normalise_address("  Customer@Example.COM ") == "customer@example.com"


# --- Settings where live data is held (coherence check 7) ---------------------------------------

REAL_SENDER = "apps.example.ProviderEmailSender"  # a provider adapter (ARC.19), never imported


@pytest.mark.parametrize(
    "sender", ["apps.core.mail.ConsoleEmailSender", "apps.core.mail.MemoryEmailSender", ""]
)
def test_live_data_refuses_the_console_and_test_senders(sender: str) -> None:

    with override_settings(
        LIVE_DATA=True, EMAIL_SENDER=sender, DEFAULT_FROM_EMAIL="no-reply@example.com"
    ):
        messages = check_email_settings(None)
    assert [m.id for m in messages] == ["core.E501"]
    assert isinstance(messages[0], checks.Error)


@pytest.mark.parametrize("address", ["no-reply@localhost", "", "No-Reply@LOCALHOST "])
def test_live_data_refuses_a_localhost_or_empty_sender_address(address: str) -> None:

    with override_settings(LIVE_DATA=True, EMAIL_SENDER=REAL_SENDER, DEFAULT_FROM_EMAIL=address):
        messages = check_email_settings(None)
    assert [m.id for m in messages] == ["core.E502"]


def test_live_data_with_a_provider_and_a_real_address_passes() -> None:

    with override_settings(
        LIVE_DATA=True, EMAIL_SENDER=REAL_SENDER, DEFAULT_FROM_EMAIL="no-reply@example.com"
    ):
        assert check_email_settings(None) == []


def test_without_live_data_the_console_sender_is_allowed() -> None:
    """Development, tests and STAGE (synthetic data) keep the console sender until ARC.19."""

    with override_settings(
        LIVE_DATA=False,
        EMAIL_SENDER="apps.core.mail.ConsoleEmailSender",
        DEFAULT_FROM_EMAIL="no-reply@localhost",
    ):
        assert check_email_settings(None) == []


def test_the_email_check_is_registered() -> None:

    registered: list[object] = list(checks.registry.registry.get_checks())
    assert check_email_settings in registered

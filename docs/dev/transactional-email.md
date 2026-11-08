# Transactional email

Every email the platform sends (one-time codes, reminders, order emails) goes through an outbox
on the task queue (backlog ARC.16, spec 01 §1.2). The provider sits behind the `EmailSender`
interface. Its adapter, the DNS records (DKIM, SPF, DMARC) and the provider webhook registration
come with ARC.19, once the owner picks the EU provider (owner question Q-48).

## Sending an email

```python
from apps.core import services

services.enqueue_email(
    recipient=user.email,
    template_key="orders.confirmation",  # templates in email/orders/confirmation/
    language=user.preferred_language,
    context={"order_number": order.number, "total": str(order.total)},
    idempotency_key=f"order:{order.public_id}:confirmation",
)
```

- Call it inside the transaction that creates the reason for the email: the outbox row and the
  `core.send_email` job are written in that transaction, so a rollback drops both.
- `context` is a snapshot of plain JSON values (Decimals become strings). It is stored encrypted
  at rest, since it can hold a one-time code or personal data, and it never appears in a
  `ChangeSet` or in the history.
- `idempotency_key` is optional. When set, a second call with the same key changes nothing.
- An address on the suppression list gets a `suppressed` row and no job.
- `dry_run=True` returns the `ChangeSet` without writing anything.

## Templates

Each template key `app.name` has three files under `templates/email/app/name/`: `subject.txt`,
`body.txt` and `body.html`. Texts use `{% translate %}` and `{% blocktranslate %}`, rendered with
the email's language active. A missing translation shows the EN source text, and a language that
is not in `settings.LANGUAGES` renders in EN (spec 09). A file under `<lang>/` (for example
`email/app/name/it/subject.txt`) replaces the gettext version for that language. The text parts
are not HTML-escaped. The context also gets `email_language`.

The HTML body must pass `apps.core.mail.html_violations`:

- **No images and no remote resources.** That rules out tracking pixels in transactional and
  service emails (Garante provision 284 of 2026-04-17, spec 11). It also rules out `<img>`,
  `srcset`, `background`, `<link>` and CSS `url()`.
- **Nothing the CSP would block**, following the SEC.1 rules: no `<script>`, no `<style>`, no
  `style=` attributes, no `on*=` event handlers, and no `javascript:` or `data:` links.

An email that fails the check is marked `failed` and never sent. The integration test scans
every `email/**/body.html` in the apps and themes as well.

## Delivery, retries and idempotency

The `core.send_email` job calls `services.deliver_email`, which locks the row while it sends:

| Result | Status | Next step |
|---|---|---|
| Accepted | `sent` | provider message id stored |
| `TransientSendError` (timeout, rate limit, outage) | `retrying` | retried after 1, 2, 4, 8 and 16 minutes (`mail.retry_delay`, the same schedule the queue applies), then `failed` |
| `PermanentSendError` (invalid address, rejected) | `failed` | no retry |
| Address suppressed meanwhile | `suppressed` | no send |

An email that is `sent`, `failed` or `suppressed` is never sent again, whatever replays the job.
The provider also receives the email's public id as an idempotency key. That covers a crash
after the provider accepted the email but before our commit. Every status change adds a row to
the append-only `OutboxEmailEvent` history.

Backends: `apps.core.mail.ConsoleEmailSender` (default; prints the text part) and
`MemoryEmailSender` (tests; `fail_next(...)` scripts provider failures). Pick one with
`MPMV_EMAIL_SENDER`. The sender address comes from `MPMV_EMAIL_FROM`.

## Bounces and complaints

`POST /webhooks/email/` takes provider feedback. It returns 404 while
`MPMV_EMAIL_WEBHOOK_SECRET` is empty. Until ARC.19 it reads the provider-neutral stub format
(`apps.core.mail.SignedJsonFeedback`):

- headers `X-Mpmv-Timestamp` (Unix time, at most 5 minutes old) and `X-Mpmv-Signature`, the
  hex HMAC-SHA256 of `<timestamp>.<body>` with the secret;
- body `{"events": [{"type": "hard_bounce" | "soft_bounce" | "complaint", "recipient": ...,
  "message_id": ..., "detail": ...}]}`.

A provider adapter implements the same `FeedbackParser` interface (`verify`, `parse`) and is
selected with `MPMV_EMAIL_FEEDBACK_PARSER`. `services.record_email_feedback` adds the event to
the email's history. A hard bounce or a complaint also puts the address on the suppression list
(`EmailSuppression`, audited as `core.email.suppressed`), and later emails to it are not sent. A
soft bounce is recorded in the history only.

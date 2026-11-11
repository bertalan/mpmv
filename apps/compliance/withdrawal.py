"""The online withdrawal function (CMP.6 2/2, spec 11 and spec 07).

Directive 2011/83/EU art. 11a (added by 2023/2673; Italy: article 54-bis of the Codice del
Consumo): a consumer who bought online can declare the withdrawal on the interface, in two steps
(a button, then a confirmation), and receives an acknowledgement on a durable medium with the
content, the date and the time. This module is the service behind the pages in `views.py`:

- `find_order`: the consumer identifies the contract with the order number and the email on the
  order, and nothing else (minimal data). A guest order works the same as an account order.
- `options`: per vendor order, whether the window is open (the pure rule is
  `engine.withdrawal_window`) and whether a statement already exists. A withdrawal targets the
  vendor order, never the whole order: each vendor is a separate seller and a separate contract.
- `submit_withdrawal`: records one `WithdrawalStatement` per chosen vendor order, audits it,
  queues the acknowledgement to the buyer and a notice to each vendor. Mutating service: it
  accepts `dry_run` and returns a ChangeSet.

What it does not do: move an order state, refund or authorise a return. That is the returns flow
(ORD.8), which reads the statements. Which goods are excluded from the right (directive art.
16) and who bears the return cost are the vendor's call at that step; the statement is recorded
for any vendor order whose window is open, because the consumer's declaration is not the vendor's
to refuse.

Settings: `WITHDRAWAL_PERIOD_DAYS` (default 14, never below 14) and
`LEGAL_GUARANTEE_MONTHS` (default 24).
"""

import datetime as dt
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.privacy import keyed_hash
from apps.core.services import consume_rate_limit, run_mutation
from apps.core.services import enqueue_email as _enqueue_email
from apps.orders.models import CustomerTaxStatus, Order, PrivacyStatus, VendorOrder
from apps.orders.tz import business_tz

from . import engine
from .models import WithdrawalStatement

logger = logging.getLogger(__name__)

MODEL: Final = "compliance.withdrawalstatement"
WITHDRAWAL_SUBMITTED: Final = "compliance.withdrawal.submitted"
ACK_TEMPLATE: Final = "compliance.withdrawal_acknowledgement"
VENDOR_TEMPLATE: Final = "compliance.withdrawal_vendor_notice"

TOKEN_SALT: Final = "compliance.withdrawal"  # noqa: S105 (a signing salt, not a secret)
TOKEN_MAX_AGE: Final = 30 * 60  # seconds the choice page stays valid after the lookup

LOOKUP_RATE_LIMIT: Final = 10
LOOKUP_RATE_WINDOW: Final = dt.timedelta(minutes=15)


class WithdrawalError(Exception):
    """A statement that cannot be recorded. `code` is one of: nothing_selected,
    unknown_vendor_order, not_available, already_withdrawn."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def period_days() -> int:
    return engine.effective_withdrawal_days(getattr(settings, "WITHDRAWAL_PERIOD_DAYS", 14))


def legal_guarantee_months() -> int:
    return int(getattr(settings, "LEGAL_GUARANTEE_MONTHS", 24))


# --- Identifying the contract


def find_order(number: str, email: str) -> Order | None:
    """The active order with this number and email (case-insensitive), else None. An order whose
    personal data was restricted or erased (D-30) is never found: there is no email to match."""
    number, email = number.strip(), email.strip()
    if not number or not email:
        return None
    return Order.objects.filter(
        number=number, email__iexact=email, privacy_status=PrivacyStatus.ACTIVE
    ).first()


def make_token(order: Order) -> str:
    """A signed, short-lived proof that the holder identified this order a moment ago."""
    return signing.dumps({"order": str(order.public_id)}, salt=TOKEN_SALT)


def read_token(token: str, *, max_age: int = TOKEN_MAX_AGE) -> Order | None:
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=max_age)
        public_id = uuid.UUID(str(data["order"]))
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        return None
    return Order.objects.filter(public_id=public_id, privacy_status=PrivacyStatus.ACTIVE).first()


def lookup_retry_after(ip: str | None) -> dt.timedelta:
    """Count one lookup from `ip`; zero when allowed, else the time to wait."""
    return consume_rate_limit(
        f"withdrawal:ip:{keyed_hash(ip or 'unknown', purpose='withdrawal-ip')}",
        limit=LOOKUP_RATE_LIMIT,
        window=LOOKUP_RATE_WINDOW,
    )


def captcha_retry_after(ip: str | None) -> dt.timedelta:
    """Count one refused captcha from `ip` (a larger, separate budget)."""
    return consume_rate_limit(
        f"withdrawal:captcha:{keyed_hash(ip or 'unknown', purpose='withdrawal-ip')}",
        limit=LOOKUP_RATE_LIMIT * 4,
        window=LOOKUP_RATE_WINDOW,
    )


# --- What can be withdrawn from


@dataclass(frozen=True)
class Option:
    vendor_order: VendorOrder
    window: engine.WithdrawalWindow
    withdrawn: bool
    vendor_name: str
    lines: tuple[dict[str, Any], ...]


def _vendor_name(sub: VendorOrder) -> str:
    return str(sub.vendor_snapshot.get("display_name") or sub.vendor.display_name)


def _lines(sub: VendorOrder) -> tuple[dict[str, Any], ...]:
    return tuple(
        {"title": line.title, "variant": line.variant_label, "quantity": line.quantity}
        for line in sub.lines.order_by("id")
    )


def options(order: Order, *, now: dt.datetime | None = None) -> list[Option]:
    """The order's vendor orders with their windows, oldest first."""
    now = now or timezone.now()
    withdrawn = set(
        WithdrawalStatement.objects.filter(order=order).values_list("vendor_order_id", flat=True)
    )
    result = []
    for sub in order.vendor_orders.select_related("vendor").order_by("id"):
        window = engine.withdrawal_window(
            is_consumer=order.customer_tax_status == CustomerTaxStatus.B2C,
            contract_at=order.paid_at,
            delivered_at=sub.delivered_at,
            vendor_order_state=sub.state,
            period_days=period_days(),
            now=now,
            tz=business_tz(),
        )
        result.append(Option(sub, window, sub.pk in withdrawn, _vendor_name(sub), _lines(sub)))
    return result


# --- The statement


def display_time(moment: dt.datetime) -> str:
    return f"{moment.astimezone(business_tz()):%Y-%m-%d %H:%M:%S %Z}"


def _enqueue(**kwargs: Any) -> bool:
    """Queue one email; a refused one (malformed recipient) is logged and skipped, so it never
    rolls back the statement. Returns whether it was queued."""
    try:
        _enqueue_email(**kwargs)
    except ValueError as exc:
        logger.warning(
            "withdrawal email %s not queued: %s", kwargs.get("template_key"), exc.__class__.__name__
        )
        return False
    return True


def submit_withdrawal(
    order: Order,
    vendor_order_ids: Sequence[uuid.UUID | str],
    *,
    ctx: AuditContext,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Record the consumer's withdrawal from the chosen vendor orders of `order`.

    Raises `WithdrawalError` (nothing is written) when nothing is chosen, an id is not a vendor
    order of this order, a window is closed, or a statement already exists. Otherwise writes one
    statement per vendor order, audits each, and queues one acknowledgement to the buyer (listing
    every statement, with date and time) and one notice per vendor, all in one transaction.
    """
    now = now or timezone.now()
    if not vendor_order_ids:
        raise WithdrawalError("nothing_selected")
    try:
        wanted = list(dict.fromkeys(uuid.UUID(str(i)) for i in vendor_order_ids))
    except ValueError:
        raise WithdrawalError("unknown_vendor_order") from None
    by_id = {o.vendor_order.public_id: o for o in options(order, now=now)}
    chosen = []
    for public_id in wanted:
        option = by_id.get(public_id)
        if option is None:
            raise WithdrawalError("unknown_vendor_order")
        if option.withdrawn:
            raise WithdrawalError("already_withdrawn")
        if not option.window.is_open:
            raise WithdrawalError("not_available")
        chosen.append(option)

    references = {o.vendor_order.pk: uuid.uuid4() for o in chosen}
    changes = ChangeSet()
    for option in chosen:
        changes.create(
            MODEL,
            str(references[option.vendor_order.pk]),
            {
                "order_number": order.number,
                "vendor_order": str(option.vendor_order.public_id),
                "submitted_at": now,
                "period_closes_at": option.window.closes_at,
                "language": order.language,
            },
        )

    def apply() -> None:
        stamp = display_time(now)
        created = []
        for option in chosen:
            created.append(
                WithdrawalStatement.objects.create(
                    public_id=references[option.vendor_order.pk],
                    order=order,
                    vendor_order=option.vendor_order,
                    submitted_at=now,
                    period_closes_at=option.window.closes_at,
                    language=order.language,
                )
            )
        queued = _enqueue(
            recipient=order.email,
            template_key=ACK_TEMPLATE,
            language=order.language,
            idempotency_key=f"withdrawal:{created[0].public_id}:ack",
            context={
                "order_number": order.number,
                "customer_name": order.customer_name,
                "submitted_at": stamp,
                "statements": [
                    {
                        "reference": str(s.public_id),
                        "vendor_name": o.vendor_name,
                        "lines": list(o.lines),
                    }
                    for s, o in zip(created, chosen, strict=True)
                ],
            },
        )
        if queued:
            WithdrawalStatement.objects.filter(pk__in=[s.pk for s in created]).update(
                acknowledged_at=now
            )
        for statement, option in zip(created, chosen, strict=True):
            sub = option.vendor_order
            if sub.vendor.contact_email:
                _enqueue(
                    recipient=sub.vendor.contact_email,
                    template_key=VENDOR_TEMPLATE,
                    language=settings.LANGUAGE_CODE,  # vendors have no language of their own yet
                    idempotency_key=f"withdrawal:{statement.public_id}:vendor",
                    context={
                        "order_number": order.number,
                        "reference": str(statement.public_id),
                        "vendor_name": option.vendor_name,
                        "submitted_at": stamp,
                        "lines": list(option.lines),
                    },
                )

    try:
        with transaction.atomic():
            return run_mutation(
                changes, apply, ctx=ctx, action=WITHDRAWAL_SUBMITTED, dry_run=dry_run
            )
    except IntegrityError:  # two submissions raced: the unique constraint let one through
        raise WithdrawalError("already_withdrawn") from None


# --- Pre-contractual information


def precontractual(language: str) -> engine.PreContractualInfo:
    """The withdrawal and legal guarantee blocks of the pre-contractual information (art.
    49(1)(h)): the period, where the function is and its labels."""
    return engine.precontractual_info(
        language=language,
        period_days=period_days(),
        withdrawal_url=reverse("withdrawal"),
        legal_guarantee_months=legal_guarantee_months(),
    )

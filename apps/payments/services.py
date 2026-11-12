"""Payment monitoring (ORD.10): the periodic check for payments without a transfer.

The payment and transfer models arrive with PAY.1-PAY.3 and the ledger. Until then the check runs
over a `PaymentSource`, the one query the models must answer; `payment_source()` is the single
place PAY.3 wires the real one in.
"""

import datetime as dt
from typing import Protocol

from django.conf import settings
from django.utils import timezone

from apps.core.alerts import raise_alert
from apps.core.opschecks import Alert

from .engine import PaidPayment, payment_without_transfer_alert

DEFAULT_GRACE_MINUTES = 60


class PaymentSource(Protocol):
    def succeeded_payments(self, *, since: dt.datetime) -> list[PaidPayment]:
        """Payments that succeeded at or after `since`, each flagged with whether a vendor
        transfer exists for it (refunded payments excluded by the implementation)."""
        ...


class NoPaymentSource:
    """Placeholder until PAY.3: there are no payments to check."""

    wired = False

    def succeeded_payments(self, *, since: dt.datetime) -> list[PaidPayment]:
        return []


def payment_source() -> PaymentSource:
    return NoPaymentSource()


def grace_period() -> dt.timedelta:
    return dt.timedelta(
        minutes=int(getattr(settings, "PAYMENT_TRANSFER_GRACE_MINUTES", DEFAULT_GRACE_MINUTES))
    )


def check_payments_without_transfer(
    source: PaymentSource | None = None, *, now: dt.datetime | None = None
) -> Alert | None:
    """Raise the alert (log, tracker, staff e-mail through OPS.2) when a succeeded payment has no
    transfer after the grace period; returns it, or None when all is well. Looks back
    `PAYMENT_TRANSFER_LOOKBACK_DAYS` (30) days."""
    moment = now or timezone.now()
    lookback = dt.timedelta(days=int(getattr(settings, "PAYMENT_TRANSFER_LOOKBACK_DAYS", 30)))
    chosen = source or payment_source()
    alert = payment_without_transfer_alert(
        chosen.succeeded_payments(since=moment - lookback), moment, grace=grace_period()
    )
    if alert is not None:
        raise_alert(alert, now=moment)
    return alert

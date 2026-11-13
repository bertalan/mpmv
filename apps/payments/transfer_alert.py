"""Pure rule: a payment that succeeded and has no vendor transfer after a grace period (ORD.10).

No Django and no ORM: the services read the rows and hand them over as `PaidPayment`.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from apps.core.opschecks import Alert

ALERT_KEY = "payments:no-transfer"
LISTED = 10  # references named in the alert


@dataclass(frozen=True)
class PaidPayment:
    """A succeeded payment, as the alert rule sees it."""

    reference: str
    succeeded_at: dt.datetime
    has_transfer: bool


def payments_without_transfer(
    payments: Iterable[PaidPayment], now: dt.datetime, *, grace: dt.timedelta
) -> list[PaidPayment]:
    """Succeeded payments with no transfer once `grace` has passed, oldest first. A payment
    exactly `grace` old is still inside the grace period."""
    late = [p for p in payments if not p.has_transfer and now - p.succeeded_at > grace]
    return sorted(late, key=lambda p: (p.succeeded_at, p.reference))


def payment_without_transfer_alert(
    payments: Iterable[PaidPayment], now: dt.datetime, *, grace: dt.timedelta
) -> Alert | None:
    late = payments_without_transfer(payments, now, grace=grace)
    if not late:
        return None
    return Alert(
        ALERT_KEY,
        "Payments succeeded without a matching vendor transfer",
        {
            "count": str(len(late)),
            "grace_minutes": str(int(grace.total_seconds() // 60)),
            "oldest_succeeded_at": late[0].succeeded_at.isoformat(),
            "references": ", ".join(p.reference for p in late[:LISTED]),
        },
    )

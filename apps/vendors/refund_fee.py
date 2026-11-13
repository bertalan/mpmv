"""Pure refund fee (VEN.33, PAY.4; no ORM, no I/O).

The refund fee is the real provider cost of the refund or chargeback plus the plan's surcharge
(default EUR 2.00), charged on every refund event, rounded UP to the whole euro. The ceil is the
one explicit exception to `ROUND_HALF_UP`. It is always charged to the vendor, whatever the cause,
except a refund caused by a platform malfunction or error (L-10): staff set an exclusion with a
required reason (the service audits it). The surcharge is versioned with an effective date and a
change needs 15 days' notice.
"""

import dataclasses
import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import ROUND_CEILING, Decimal
from typing import Final

from apps.core.money import round_line

EURO: Final = Decimal("1")
DEFAULT_SURCHARGE: Final = Decimal("2.00")
NOTICE_DAYS: Final = 15
SNAPSHOT_KEY: Final = "refund_fee_surcharge"


class ReasonRequired(ValueError):
    """A platform-error exclusion needs a reason."""


class NoticeTooShort(ValueError):
    """A surcharge version takes effect less than 15 days after its notice date."""


@dataclasses.dataclass(frozen=True)
class RefundFeeRule:
    """The refund-fee parameters of a plan, as snapshotted on the order."""

    surcharge: Decimal = DEFAULT_SURCHARGE

    def __post_init__(self) -> None:
        if self.surcharge < 0:
            raise ValueError("The refund-fee surcharge cannot be negative.")
        if self.surcharge != round_line(self.surcharge):
            raise ValueError("The refund-fee surcharge allows at most 2 decimals.")

    def snapshot(self) -> dict[str, str]:
        return {SNAPSHOT_KEY: str(round_line(self.surcharge))}

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, str]) -> "RefundFeeRule":
        if SNAPSHOT_KEY not in snapshot:
            raise ValueError(f"The rule snapshot has no {SNAPSHOT_KEY}.")
        return cls(surcharge=Decimal(snapshot[SNAPSHOT_KEY]))


@dataclasses.dataclass(frozen=True)
class PlatformErrorExclusion:
    """Set only by platform staff, with a required reason; audited by the service."""

    reason: str
    set_by: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ReasonRequired("A platform-error exclusion needs a reason.")


def refund_fee(
    provider_cost: Decimal,
    rule: RefundFeeRule,
    *,
    platform_error: PlatformErrorExclusion | None = None,
) -> Decimal:
    """Provider cost plus surcharge, rounded up to the whole euro; 0.00 on a platform error."""
    if provider_cost < 0:
        raise ValueError("The provider cost cannot be negative.")
    if platform_error is not None:
        return Decimal("0.00")
    total = provider_cost + rule.surcharge
    return round_line(total.quantize(EURO, rounding=ROUND_CEILING))


def check_notice(notice_date: dt.date, effective_date: dt.date) -> None:
    """A new surcharge version needs at least 15 days between notice and effect."""
    if (effective_date - notice_date).days < NOTICE_DAYS:
        raise NoticeTooShort(f"A surcharge change needs {NOTICE_DAYS} days' notice.")


@dataclasses.dataclass(frozen=True)
class SurchargeVersion:
    surcharge: Decimal
    effective_from: dt.date


def version_in_force(versions: Iterable[SurchargeVersion], on: dt.date) -> SurchargeVersion:
    """The version whose effective date covers `on` (the order's creation date)."""
    covering = [v for v in versions if v.effective_from <= on]
    if not covering:
        raise LookupError("No surcharge version is in force on that date.")
    return max(covering, key=lambda v: v.effective_from)

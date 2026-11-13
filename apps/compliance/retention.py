"""The retention job (CMP.4 2/2, owner decisions D-29 and D-30, spec 11 "Retention per data type").

One run applies the privacy lifecycle with the ORD.2 scrub services (`restrict_order`,
`anonymise_order`), which are the only code that ever rewrites an order and which refuse before
the date; and completes the account erasures that waited for an open order. Nothing here touches a
fiscal field itself. A dry run lists the candidates and writes nothing. A real run writes one
`compliance.retention_run` audit entry with counts only (the scrub services audit each order by
public id).
"""

import datetime as dt
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.core.audit import AuditContext, record
from apps.orders import engine
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.orders.tz import business_tz

from . import gdpr
from .models import ErasureRequest

ACTION_RUN = "compliance.retention_run"


@dataclass
class RetentionResult:
    restricted: int = 0
    anonymised: int = 0
    erasures_completed: int = 0
    skipped: int = 0
    candidates: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "restricted": self.restricted,
            "anonymised": self.anonymised,
            "erasures_completed": self.erasures_completed,
            "skipped": self.skipped,
            "candidates": len(self.candidates),
        }


def _active_phase_months() -> int:
    configured = int(getattr(settings, "ORDER_ACTIVE_PHASE_MONTHS", 24))
    return max(configured, engine.MIN_ACTIVE_PHASE_MONTHS)


def _due(order: Order, now: dt.datetime) -> str:
    """ "anonymise", "restrict" or "" (nothing yet), from the pure date rules."""
    if order.privacy_status == "anonymized":
        return ""
    tz = business_tz()
    if engine.anonymisable_at_once(
        order.state, order.paid_at, order.created_at, now
    ) or engine.anonymisation_due(order.created_at, now, tz):
        return "anonymise"
    if (
        order.privacy_status == "active"
        and order.state in gdpr.CONCLUDED_ORDER_STATES
        and engine.restriction_due(order.created_at, now, _active_phase_months(), tz)
    ):
        return "restrict"
    return ""


def run_retention(
    *, ctx: AuditContext, dry_run: bool = False, now: dt.datetime | None = None
) -> RetentionResult:
    """Apply the lifecycle to every order and pending erasure that is due at `now`."""
    stamp = now or timezone.now()
    result = RetentionResult()
    orders = Order.objects.exclude(privacy_status="anonymized").order_by("id")
    for order in orders.iterator():
        action = _due(order, stamp)
        if not action:
            continue
        result.candidates.append(str(order.public_id))
        if dry_run:
            continue
        try:
            if action == "anonymise":
                order_services.anonymise_order(order, ctx=ctx, now=stamp)
                result.anonymised += 1
            else:
                order_services.restrict_order(order, ctx=ctx, now=stamp)
                result.restricted += 1
        except order_services.ScrubNotDue:
            result.skipped += 1  # the database clock disagrees: leave it for the next run
    for request in ErasureRequest.objects.filter(completed_at__isnull=True).select_related("user"):
        result.candidates.append(f"erasure:{request.public_id}")
        if dry_run:
            continue
        try:
            gdpr.anonymise_customer(request.user, ctx=ctx, now=stamp)
        except gdpr.VendorLinkedUser:
            result.skipped += 1
            continue
        request.refresh_from_db()
        if request.completed_at is not None:
            result.erasures_completed += 1
    if not dry_run:
        record(ctx, ACTION_RUN, "compliance.retention", "run", {}, result.counts())
    return result

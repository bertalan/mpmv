"""Refunds of a vendor order, through and outside the platform (PAY.4, D-05, D-41, L-10).

Who may start one, and how:

- `refund_through_platform`: a member of the vendor confirms a refund in its own area (by LINE,
  by QUANTITY or by AMOUNT). The platform refunds the buyer through the provider and takes the
  vendor's share back with ONE reverse transfer of exactly that vendor's transfer. Never another
  vendor's, and none when the transfer was never created (see below).
- `refund_by_platform`: platform staff refund on the platform's own initiative, only for its own
  error (the refund fee is then excluded, with a required reason, L-10) or a vendor default, and
  only while the operator setting (ARC.7) lists the case.
- `mark_refunded_outside`: the vendor refunded the buyer itself. NOTHING moves: no ledger line, no
  provider call, no state change. The mark carries the declared refund date and method and is
  audited; the platform is not liable for that refund, the DAC7 consideration the platform reports
  is unchanged (CMP.5 clause (i)), and the operator setting may forbid the mark. A buyer complaint
  that it was not refunded goes to `refund_by_platform` (vendor default).
- A payment that succeeded twice is a different case, `apps.payments.duplicates`.

Ledger lines of one refund event (signed from the vendor's side): `refund` -gross; `commission_
reversal` and `payment_fee_reversal` (the collection cost) + pro rata on the refunded share, only
when the operator reverses fees on a vendor-side refund (platform-initiated refunds always do);
`refund_fee` - (VEN.33: provider cost from the balance transaction + the surcharge, ceil to the
euro, charged to the VENDOR only, 0 on a platform error); `transfer_reversal` + the amount the
provider took back from the vendor's transfer (the refunded gross minus the reversals, never more
than the transfer still holds). The refund fee is not reversed from the account: it stays a
debit on the vendor's balance, settled against later transfers (the platform service line of the
CMP.2 statement).

A refund BEFORE the pending transfer needs no reverse transfer: `settlement.transferable_net`
reduces the transfer by the refund lines, or drops it when nothing is left.

Safety: the Order and then the vendor order row are locked for the whole event (the lock order of
the order transitions); the provider call carries a key built from the caller's `request_id`, so
a repeat after a lost answer replays the same refund and the ledger keys make the lines
idempotent. The provider is called inside the transaction on purpose (like the transfer): a
failure raises `RefundFailed` and nothing is written. Once the provider has refunded, the Refund
and its lines are always kept: a failed reversal leaves `reversal_pending`, a failed state update
leaves `states_pending`, and the sweep or a replay of the request finishes them.
"""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from django.utils import timezone

from apps.core.alerts import raise_alert
from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import PLATFORM_ERROR, PLATFORM_REFUND_CASES, AuditLog
from apps.core.money import round_line
from apps.core.opschecks import Alert
from apps.core.services import audit_changes, current_operator
from apps.orders import amendment_engine as am
from apps.orders import amendments as orders_amendments
from apps.orders import services as orders_services
from apps.orders.models import (
    Order,
    OrderAmendment,
    OrderLine,
    VendorOrder,
    VendorOrderVersion,
)
from apps.payments import registry
from apps.payments import settlement as payments_settlement
from apps.payments.engine import (
    ProviderError,
    RefundRequest,
    TransferReversal,
    TransferReversalFailed,
)
from apps.payments.models import Payment
from apps.vendors import engine as vendor_engine
from apps.vendors.commission import RefundEvent, compute_commission, refund_lines
from apps.vendors.refund_fee import (
    CustomerRequestedAmendment,
    FeeExclusion,
    PlatformErrorExclusion,
    RefundFeeRule,
)
from apps.vendors.services import has_vendor_permission

from . import engine, refund_engine, services, settlement
from .models import FeeExclusionReason, LedgerEntry, Refund, RefundChannel
from .refund_engine import InvalidRefundRequest, LineFigures, RefundSelection

REFUND_MODEL = "ledger.refund"
REFUND_RECORDED = "ledger.refund_recorded"
REFUND_MARKED_OUTSIDE = "ledger.refund_marked_outside"
REFUND_FEE_EXCLUDED = "ledger.refund_fee_excluded"
REFUND_REVERSAL_PENDING = "ledger.refund_reversal_pending"
REFUND_REVERSAL_DONE = "ledger.refund_reversal_done"
REFUND_STATES_PENDING = "ledger.refund_states_pending"
ZERO = Decimal("0.00")
MOVED = (RefundChannel.THROUGH_PLATFORM, RefundChannel.PLATFORM_INITIATED)
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.-]{8,64}")
METHOD_MAX = 64
REASON_MAX = 2000
SNAPSHOT_SURCHARGE_KEY = "refund_fee_surcharge"
# ORD.20 (joint opinion 2026-11-16; art. 61 c. 4 Codice del consumo): an amendment refund is due
# within 14 days of the event, never later, and staff are alerted 4 days before that.
REFUND_DUE_DAYS = 14
REFUND_ALERT_DAYS_BEFORE = 4
CAUSE_AMENDMENT = "amendment"
CAUSE_CANCEL_REST = "cancel_rest"
SUCCEEDED = "succeeded"
FAILED_STATUSES = frozenset({"failed", "canceled"})
REFUND_PENDING_SUBMISSION = "ledger.refund_pending_submission"
REFUND_SUBMISSION_FAILED = "ledger.refund_submission_failed"


class RefundRefused(ValueError):
    """The refund is not allowed (state, operator setting, amount, cause); nothing was written."""


class RefundFailed(Exception):
    """The provider refused or failed the refund; nothing was written, the same request can be
    sent again."""


class StaffRequired(PermissionDenied):
    """Only active platform staff start a platform-initiated refund."""


@dataclass
class RefundChangeSet(ChangeSet):
    """The ChangeSet of a refund call plus the `Refund` row it wrote (None on a dry run, on a
    replay, or when the call was refused before writing)."""

    refund: Refund | None = None


def _check_request_id(request_id: str) -> None:
    if not _REQUEST_ID.fullmatch(request_id):
        raise RefundRefused("the request id must be 8 to 64 letters, digits, '.', '_' or '-'")


def _lock(vendor_order: VendorOrder) -> VendorOrder:
    """Lock the Order, then the VendorOrder: the order of `transition_vendor_order` and
    `apply_refund_to_states`, so a refund and a fulfilment step never deadlock."""
    Order.objects.select_for_update().filter(pk=vendor_order.order_id).first()
    return (
        VendorOrder.objects.select_for_update(of=("self",))
        .select_related("order", "vendor")
        .get(pk=vendor_order.pk)
    )


def _check_paid(sub: VendorOrder) -> None:
    if sub.order.state not in orders_services.PAID_OR_LATER:
        raise RefundRefused(f"the order is {sub.order.state}: only a paid order is refunded")


def _line_figures(sub: VendorOrder) -> dict[int, LineFigures]:
    """Each order line as the customer has it now: its quantity and gross in the package's
    current version (ORD.20). After an amendment lowered a price or a quantity, a refund by line
    reads the amended line, never the line as placed; a line an amendment removed has nothing
    left (quantity 0). Without amendments that is the placed line (v1)."""
    lines = list(OrderLine.objects.filter(vendor_order=sub))
    version = (
        VendorOrderVersion.objects.filter(pk=sub.current_version_id).first()
        if sub.current_version_id
        else None
    )
    if version is None or version.number == 1:
        return {
            line.pk: LineFigures(
                line.pk, line.quantity, round_line(line.net_total + line.tax_amount)
            )
            for line in lines
        }
    current = {line.line_key: line for line in am.VersionSnapshot.from_json(version.snapshot).lines}
    figures: dict[int, LineFigures] = {}
    for line in lines:
        found = current.get(str(line.public_id))
        figures[line.pk] = (
            LineFigures(line.pk, found.quantity, round_line(found.net + found.tax))
            if found is not None
            else LineFigures(line.pk, 0, ZERO)
        )
    return figures


def _already(sub: VendorOrder, channels: tuple[str, ...] = MOVED) -> dict[int, int]:
    """Units already refunded per line, by the refunds other than amendments: the units an
    amendment took out are already out of the current version's quantity."""
    return refund_engine.refunded_quantities(
        request
        for request in _group(sub, channels).values_list("request", flat=True)
        if request.get("mode") != CAUSE_AMENDMENT
    )


def _group(sub: VendorOrder, channels: tuple[str, ...]) -> "QuerySet[Refund]":
    return Refund.objects.filter(vendor_order=sub, channel__in=channels)


def _sum(rows: "QuerySet[Any]", field: str) -> Decimal:
    return round_line(rows.aggregate(total=Sum(field))["total"] or ZERO)


def _gross_of(sub: VendorOrder, selection: RefundSelection, channels: tuple[str, ...]) -> Decimal:
    return refund_engine.refunded_gross_of(selection, _line_figures(sub), _already(sub, channels))


def _stored(
    sub: VendorOrder, selection: RefundSelection, channels: tuple[str, ...]
) -> RefundSelection:
    return refund_engine.normalised_lines(selection, _line_figures(sub), _already(sub, channels))


def _fee_rule(snapshot: dict[str, object], placed_at: dt.datetime) -> RefundFeeRule:
    """The surcharge frozen on the order's rule at placement. An order placed before it was frozen
    gets the operator version in force when the order was created, never the live value."""
    if SNAPSHOT_SURCHARGE_KEY in snapshot:
        return RefundFeeRule.from_snapshot(
            {SNAPSHOT_SURCHARGE_KEY: str(snapshot[SNAPSHOT_SURCHARGE_KEY])}
        )
    return RefundFeeRule(surcharge=round_line(current_operator(placed_at).refund_fee_surcharge))


def _snapshot_of(sub: VendorOrder) -> dict[str, object]:
    snapshot: dict[str, object] = dict(sub.commission_snapshot or {})
    if not snapshot:
        raise vendor_engine.NoCommissionRule("the vendor order has no commission snapshot")
    snapshot.setdefault("promo_label", "")
    return snapshot


def _replay(sub: VendorOrder, key: str, channel: str, selection: RefundSelection) -> bool:
    """True when this request id was already carried out for the same request (a repeat); a
    request id reused for another request is refused."""
    existing = Refund.objects.filter(idempotency_key=key).first()
    if existing is None:
        return False
    if (
        existing.vendor_order_id != sub.pk
        or existing.channel != channel
        or not selection.same_request_as(existing.request)
    ):
        raise RefundRefused("this request id was used for another refund")
    return True


def _created(refund: Refund, extra: dict[str, object] | None = None) -> dict[str, object]:
    return {
        VENDOR_FIELD: str(refund.vendor.public_id),
        "vendor_order": str(refund.vendor_order.public_id),
        "order": refund.vendor_order.order.number,
        "channel": refund.channel,
        "cause": refund.cause,
        "request": refund.request,
        "refunded_gross": refund.refunded_gross,
        "commission_reversal": refund.commission_reversal,
        "payment_fee_reversal": refund.payment_fee_reversal,
        "refund_fee": refund.refund_fee,
        "provider_cost": refund.provider_cost,
        "transfer_reversed": refund.transfer_reversed,
        "provider_refund_ref": refund.provider_refund_ref,
        "platform_error": refund.platform_error,
        "platform_error_reason": refund.platform_error_reason,
        "declared_date": refund.declared_date,
        "declared_method": refund.declared_method,
        "reason": refund.reason,
        "amendment": str(refund.amendment.public_id) if refund.amendment else "",
        "fee_exclusion_reason": refund.fee_exclusion_reason,
        "due_by": refund.due_by,
        "provider_status": refund.provider_status,
        **(extra or {}),
    }


def _payment_of(order: Order) -> Payment:
    payment = Payment.objects.filter(
        order_ref=order.number, provider_ref=order.payment_intent_ref, duplicate=False
    ).first()
    if payment is None or not order.payment_intent_ref:
        raise RefundRefused("the order has no confirmed payment to refund")
    return payment


def refund_through_platform(
    vendor_order: VendorOrder,
    selection: RefundSelection,
    *,
    request_id: str,
    actor: object,
    ctx: AuditContext,
    reason: str = "",
    dry_run: bool = False,
) -> RefundChangeSet:
    """A member of the vendor refunds the buyer through the platform (D-41 channel 1).

    The actor needs the `orders` action on the vendor (`PermissionDenied` otherwise, checked
    first). Whether the commission and collection cost go back is the operator's setting
    (`reverse_fees_on_vendor_refund`, default yes, D-05). Raises `RefundRefused` for a state, a
    request or an amount that cannot be refunded, `RefundFailed` when the provider fails,
    `InvalidRefundRequest` for a wrong selection; nothing is written in any of those cases."""
    if not has_vendor_permission(actor, vendor_order.vendor, "orders"):  # type: ignore[arg-type]
        raise PermissionDenied("Only a member of the vendor can refund its orders.")
    return _refund_money(
        vendor_order,
        selection,
        channel=RefundChannel.THROUGH_PLATFORM,
        cause="",
        request_id=request_id,
        ctx=ctx,
        reason=reason,
        exclusion_reason="",
        dry_run=dry_run,
    )


def refund_by_platform(
    vendor_order: VendorOrder,
    selection: RefundSelection,
    *,
    cause: str,
    request_id: str,
    actor: object,
    ctx: AuditContext,
    platform_error_reason: str = "",
    reason: str = "",
    dry_run: bool = False,
) -> RefundChangeSet:
    """Platform staff refund the buyer on the platform's own initiative (D-41 (1)).

    Only for `platform_error` (a non-blank `platform_error_reason` is required, it excludes the
    refund fee and is audited, L-10) or `vendor_default` (the fee is charged as usual, e.g. a
    buyer complaint that an outside refund never came). The case must be listed in the
    operator's `platform_refund_cases`. Fees are always given back pro rata. `StaffRequired`
    unless the actor is an active staff user."""
    if not (getattr(actor, "is_active", False) and getattr(actor, "is_staff", False)):
        raise StaffRequired("Only platform staff refund on the platform's own initiative.")
    if cause not in PLATFORM_REFUND_CASES:
        raise RefundRefused(f"unknown platform refund case {cause!r}")
    if cause not in current_operator().platform_refund_cases:
        raise RefundRefused(f"the operator does not allow platform refunds for {cause}")
    exclusion_reason = platform_error_reason.strip()
    if cause == PLATFORM_ERROR:
        PlatformErrorExclusion(reason=exclusion_reason, set_by=ctx.actor_label)  # raises if blank
    elif exclusion_reason:
        raise RefundRefused("only a platform error can exclude the refund fee")
    return _refund_money(
        vendor_order,
        selection,
        channel=RefundChannel.PLATFORM_INITIATED,
        cause=cause,
        request_id=request_id,
        ctx=ctx,
        reason=reason,
        exclusion_reason=exclusion_reason,
        dry_run=dry_run,
    )


@dataclass(frozen=True)
class _Plan:
    """One refund event to carry out on a locked vendor order: what it refunds and how it is
    recorded. `exclusion` drops the refund fee (a platform error, or an amendment the customer
    asked for); `due_by` is set for the refunds of an amendment and of a cancel of the rest."""

    gross: Decimal
    request: dict[str, object]
    channel: str
    cause: str
    key: str
    reason: str
    exclusion: FeeExclusion | None = None
    amendment: OrderAmendment | None = None
    due_by: dt.datetime | None = None
    # ORD.20 (owner decision on #564): an amendment gives back the commission of its recompute,
    # not the pro rata of the refunded gross.
    commission_back: Decimal | None = None


def _refund_money(
    vendor_order: VendorOrder,
    selection: RefundSelection,
    *,
    channel: str,
    cause: str,
    request_id: str,
    ctx: AuditContext,
    reason: str,
    exclusion_reason: str,
    dry_run: bool,
) -> RefundChangeSet:
    _check_request_id(request_id)
    if len(reason) > REASON_MAX:
        raise RefundRefused("the reason is too long")
    key = f"refund:{vendor_order.public_id}:{request_id}"
    with transaction.atomic():
        sub = _lock(vendor_order)
        _check_paid(sub)
        if _replay(sub, key, channel, selection):
            _finish_pending(key)
            return RefundChangeSet()
        if sub.state in {"cancelled", "refunded"}:
            raise RefundRefused(f"the vendor order is {sub.state}: nothing is left to refund")
        plan = _Plan(
            gross=_gross_of(sub, selection, MOVED),
            request=_stored(sub, selection, MOVED).snapshot(),
            channel=channel,
            cause=cause,
            key=key,
            reason=reason,
            exclusion=(
                PlatformErrorExclusion(reason=exclusion_reason, set_by=ctx.actor_label)
                if exclusion_reason
                else None
            ),
        )
        return _carry_out(sub, plan, ctx=ctx, dry_run=dry_run)


def _carry_out(
    sub: VendorOrder,
    plan: _Plan,
    *,
    ctx: AuditContext,
    dry_run: bool,
    now: dt.datetime | None = None,
    defer: bool = False,
) -> RefundChangeSet:
    """Refund `plan` on the locked vendor order `sub`: the provider refund and the reverse
    transfer in one call, then the `Refund` row, its ledger lines, the audit entries and the
    order states. A dry run returns the estimate and writes nothing. Every refund other than an
    amendment's own supersedes the package's open amendment (spec 07, Concurrency).

    `defer` (ORD.20, an amendment or a cancel of the rest): the `Refund` row is only recorded
    here, `submission_pending`, in the transaction of the change; `submit_pending_refund` calls
    the provider once that transaction committed, so a refund the provider made is never lost to
    a rollback, and the sweep retries one the provider did not take (same idempotency key)."""
    result = RefundChangeSet()
    operator = current_operator()
    gross = plan.gross
    refunded_before = _sum(_group(sub, MOVED), "refunded_gross")
    if gross > round_line(sub.gross_total - refunded_before):
        raise RefundRefused("the refund exceeds what is left of the vendor order")
    _payment_of(sub.order)
    snapshot = _snapshot_of(sub)
    rule = _fee_rule(snapshot, sub.order.created_at)
    reverse = (
        plan.channel == RefundChannel.PLATFORM_INITIATED or operator.reverse_fees_on_vendor_refund
    )
    exclusion = plan.exclusion
    planned = _figures(sub, snapshot, gross, ZERO, rule, exclusion, reverse, plan.commission_back)
    transfer = _transfer_of(sub)
    reversal_amount = _reversal_amount(planned, gross, transfer)
    platform_error = isinstance(exclusion, PlatformErrorExclusion)
    refund = Refund(
        vendor=sub.vendor,
        vendor_order=sub,
        channel=plan.channel,
        cause=plan.cause,
        idempotency_key=plan.key,
        request=plan.request,
        refunded_gross=gross,
        platform_error=platform_error,
        platform_error_reason=exclusion.reason if platform_error and exclusion else "",
        reason=plan.reason,
        created_by=ctx.actor_label[:128],
        amendment=plan.amendment,
        fee_exclusion_reason=(
            FeeExclusionReason.CUSTOMER_REQUESTED_AMENDMENT
            if isinstance(exclusion, CustomerRequestedAmendment)
            else FeeExclusionReason.NONE
        ),
        due_by=plan.due_by,
    )
    refund.commission_reversal = planned.commission_reversal
    refund.payment_fee_reversal = planned.payment_fee_reversal
    refund.refund_fee = planned.refund_fee
    refund.transfer_reversed = reversal_amount
    result.create(
        REFUND_MODEL,
        str(refund.public_id),
        _created(refund, {"estimate": True} if dry_run else None),
    )
    if dry_run:
        return result
    if plan.amendment is None:
        orders_amendments.supersede_open_amendment(sub, cause="refund", ctx=ctx)
    if defer:
        refund.submission_pending = True
        refund.save()
        recorded = ChangeSet()
        recorded.create(REFUND_MODEL, str(refund.public_id), _created(refund))
        audit_changes(recorded, ctx=ctx, action=REFUND_PENDING_SUBMISSION)
        result.changes = recorded.changes
        result.refund = refund
        return result
    try:
        changes = _send(sub, refund, ctx=ctx, now=now, exclusion=exclusion, rule=rule)
    except (ProviderError, registry.UnknownProvider) as error:
        raise RefundFailed(str(error)) from error
    result.changes = changes.changes
    result.refund = refund
    return result


def _send(
    sub: VendorOrder,
    refund: Refund,
    *,
    ctx: AuditContext,
    now: dt.datetime | None,
    exclusion: FeeExclusion | None,
    rule: RefundFeeRule,
) -> ChangeSet:
    """Call the provider for `refund` (refund and reverse transfer) and record the answer: the
    final figures with the real provider cost, the ledger lines, the audit entries and the order
    states. Raises `ProviderError` (nothing recorded) when the provider refused."""
    payment = _payment_of(sub.order)
    snapshot = _snapshot_of(sub)
    reverse = (
        refund.channel == RefundChannel.PLATFORM_INITIATED
        or current_operator().reverse_fees_on_vendor_refund
    )
    commission_back = refund.commission_reversal if refund.amendment_id else None
    gross = refund.refunded_gross
    exclude = refund.pk

    def lines_for(cost: Decimal) -> _Figures:
        return _figures(
            sub, snapshot, gross, cost, rule, exclusion, reverse, commission_back, exclude
        )

    transfer = _transfer_of(sub)
    reversal_amount = _reversal_amount(lines_for(ZERO), gross, transfer)
    try:
        answer = registry.get_provider(payment.provider).refund(
            RefundRequest(
                provider_ref=payment.provider_ref,
                amount=gross,
                currency=payment.currency,
                idempotency_key=refund.idempotency_key,
                reverse_vendor_shares=False,
                reversals=(
                    (TransferReversal(transfer.ref, reversal_amount),)
                    if transfer is not None and reversal_amount > 0
                    else ()
                ),
            )
        )
    except TransferReversalFailed as error:
        answer, pending = error.result, True  # the buyer IS refunded: record it, retry later
    else:
        pending = False
    final = lines_for(round_line(answer.provider_cost))
    refund.commission_reversal = final.commission_reversal
    refund.payment_fee_reversal = final.payment_fee_reversal
    refund.refund_fee = final.refund_fee
    refund.provider_cost = round_line(answer.provider_cost)
    refund.provider_refund_ref = answer.refund_ref
    refund.transfer_reversed = reversal_amount
    refund.reversal_pending = pending
    refund.submission_pending = False
    refund.provider_status = answer.status[:24]
    refund.succeeded_at = (now or timezone.now()) if answer.status == SUCCEEDED else None
    refund.save()
    _write_lines(sub, refund, answer.refund_ref, answer.reversal_refs, transfer, ctx)
    changes = ChangeSet()
    changes.create(REFUND_MODEL, str(refund.public_id), _created(refund))
    audit_changes(changes, ctx=ctx, action=REFUND_RECORDED)
    if pending:
        _flag_pending_reversal(refund, ctx)
    if exclusion is not None:
        flagged = ChangeSet()
        flagged.create(
            REFUND_MODEL,
            str(refund.public_id),
            {
                VENDOR_FIELD: str(sub.vendor.public_id),
                "reason": exclusion.reason,
                "set_by": exclusion.set_by,
                "amendment": str(refund.amendment.public_id) if refund.amendment else "",
            },
        )
        audit_changes(flagged, ctx=ctx, action=REFUND_FEE_EXCLUDED)
    if answer.status in FAILED_STATUSES:
        _alert_failed(refund)
    _apply_states(refund, ctx)
    return changes


def _alert_failed(refund: Refund) -> None:
    raise_alert(
        Alert(
            f"refund-failed:{refund.public_id}",
            "The payment provider reports a refund as failed or cancelled: refund the customer "
            "another way within the 14-day deadline",
            {
                "refund": str(refund.public_id),
                "order": refund.vendor_order.order.number,
                "provider_status": refund.provider_status,
            },
        )
    )


def _exclusion_of(refund: Refund) -> FeeExclusion | None:
    if refund.fee_exclusion_reason and refund.amendment is not None:
        return CustomerRequestedAmendment(
            amendment=str(refund.amendment.public_id), set_by="service:orders.amendments"
        )
    if refund.platform_error:
        return PlatformErrorExclusion(reason=refund.platform_error_reason, set_by=refund.created_by)
    return None


def submit_pending_refund(refund_id: str, *, ctx: AuditContext) -> bool:
    """Submit a recorded amendment or cancel-the-rest refund to the provider (ORD.20), after the
    change that recorded it committed. Idempotent (the provider key is the refund's). A provider
    failure keeps it `submission_pending`, audited and alerted once; the sweep retries it. True
    when it is submitted (now or before)."""
    found = (
        Refund.objects.filter(public_id=refund_id, submission_pending=True)
        .values_list("vendor_order_id", flat=True)
        .first()
    )
    if found is None:
        return True
    with transaction.atomic():
        sub = _lock(VendorOrder.objects.select_related("order").get(pk=found))
        refund = (
            Refund.objects.select_for_update(of=("self",))
            .select_related("vendor", "vendor_order__order", "amendment")
            .get(public_id=refund_id)
        )
        if not refund.submission_pending:
            return True
        rule = _fee_rule(_snapshot_of(sub), sub.order.created_at)
        try:
            with transaction.atomic():
                _send(sub, refund, ctx=ctx, now=None, exclusion=_exclusion_of(refund), rule=rule)
        except (ProviderError, registry.UnknownProvider, RefundRefused) as error:
            refund.refresh_from_db()
            if not refund.provider_status:
                refund.provider_status = "not_submitted"
                refund.save(update_fields=["provider_status"])
                flagged = ChangeSet()
                flagged.create(
                    REFUND_MODEL,
                    str(refund.public_id),
                    {VENDOR_FIELD: str(refund.vendor.public_id), "error": str(error)[:200]},
                )
                audit_changes(flagged, ctx=ctx, action=REFUND_SUBMISSION_FAILED)
                raise_alert(
                    Alert(
                        f"refund-submission:{refund.public_id}",
                        "A refund could not be submitted to the payment provider; it is retried",
                        {"refund": str(refund.public_id), "order": sub.order.number},
                    )
                )
            return False
    return True


def submit_pending_refunds() -> int:
    """The sweep: submit each refund still waiting for the provider; returns how many went."""
    ids = list(Refund.objects.filter(submission_pending=True).values_list("public_id", flat=True))
    ctx = AuditContext(actor_label="system:refund-submission", source=AuditLog.Source.SYSTEM)
    return sum(submit_pending_refund(str(public_id), ctx=ctx) for public_id in ids)


# --- ORD.20: the refunds of order amendments --------------------------------------------------


def _units_by_line(sub: VendorOrder, units: dict[str, int]) -> list[tuple[int, int]]:
    """`units` keyed by version line key (the v1 `OrderLine.public_id`) as (line pk, units);
    lines added by a later version have no order line and are left out."""
    ids = {
        str(public_id): pk
        for public_id, pk in OrderLine.objects.filter(
            vendor_order=sub, public_id__in=list(units)
        ).values_list("public_id", "pk")
    }
    return sorted((ids[key], qty) for key, qty in units.items() if key in ids)


def _removed_units(amendment: OrderAmendment) -> dict[str, int]:
    """The units the amendment takes out of each line (a removed line, a lower quantity)."""
    before = am.VersionSnapshot.from_json(amendment.from_version.snapshot)
    after = am.VersionSnapshot.from_json(amendment.preview["after_snapshot"])
    left = {line.line_key: line.quantity for line in after.lines}
    removed: dict[str, int] = {}
    for line in before.lines:
        gone = line.quantity - left.get(line.line_key, 0)
        if gone > 0:
            removed[str(line.line_key)] = gone
    return removed


def _touched_keys(amendment: OrderAmendment) -> set[str]:
    """The lines whose quantity, price or discount the amendment changes, or which it removes."""
    before = am.VersionSnapshot.from_json(amendment.from_version.snapshot)
    after = {
        line.line_key: line
        for line in am.VersionSnapshot.from_json(amendment.preview["after_snapshot"]).lines
    }
    touched: set[str] = set()
    for line in before.lines:
        new = after.get(line.line_key)
        if new is None or (new.quantity, new.unit_price_net, new.discount_net) != (
            line.quantity,
            line.unit_price_net,
            line.discount_net,
        ):
            touched.add(str(line.line_key))
    return touched


def _check_not_refunded(sub: VendorOrder, amendment: OrderAmendment) -> None:
    """Refuse an amendment that changes a line a refund already gave back in part or in full:
    the version would not know of that refund, so the customer could be refunded twice."""
    already = _already(sub)
    for line_id, _ in _units_by_line(sub, dict.fromkeys(_touched_keys(amendment), 1)):
        if already.get(line_id, 0) > 0:
            raise orders_amendments.AmendmentRefused(
                "This change touches a line that was already refunded, so it could refund the "
                "customer twice: discard the draft and refund the rest of that line instead."
            )


def _outcome(result: RefundChangeSet) -> "orders_amendments.RefundOutcome":
    created: Mapping[str, Any] = (result.changes[0].after or {}) if result.changes else {}
    refund = result.refund
    return orders_amendments.RefundOutcome(
        refund_id=str(refund.public_id) if refund else "",  # "" on a dry run: nothing recorded
        provider_ref=refund.provider_refund_ref if refund else "",
        amount=Decimal(str(created.get("refunded_gross", ZERO))),
        refund_fee=Decimal(str(created.get("refund_fee", ZERO))),
        fee_exclusion_reason=str(created.get("fee_exclusion_reason", "")),
        due_by=created.get("due_by"),
        changes=list(result.changes),
    )


def _provider_failed(error: RefundFailed) -> "orders_amendments.AmendmentRefused":
    return orders_amendments.AmendmentRefused(
        "The refund could not be submitted to the payment provider, so nothing was changed: "
        f"try again in a few minutes ({error})."
    )


def _customer_asked(amendment: OrderAmendment) -> bool:
    """Whether the amendment is one the customer asked for: a recorded customer request (channel,
    date, the customer's words) on an address change marked as asked by the customer. Read from
    the amendment only. The vendor's own record of the request is enough (owner, 2026-11-16; it is
    audited and staff can review it)."""
    words = str((amendment.customer_request or {}).get("text") or "").strip()
    # Every change must be that address change: mixed with a removal or a price change, the
    # refund is the vendor's change too and keeps the fee (second review of #564).
    return (
        bool(words)
        and bool(amendment.changes)
        and all(
            str(change.get("kind")) == am.ChangeAddress.kind
            and bool(change.get("requested_by_customer"))
            for change in amendment.changes
        )
    )


def refund_amendment(
    amendment: OrderAmendment,
    *,
    amount: Decimal,
    commission_back: Decimal,
    ctx: AuditContext,
    now: dt.datetime,
    dry_run: bool = False,
) -> "orders_amendments.RefundOutcome":
    """The PAY.4 refund event of an applied amendment's decrease (ORD.20, spec 07 §Money).

    Called by `apps.orders.amendments.apply_amendment` (setting `ORDER_AMENDMENT_REFUND_HOOK`) in
    the transaction that writes the new version, so the version and the refund are written
    together or not at all. Same event as a PAY.4 refund of the same amount (the collection
    cost pro rata, cumulatively; one refund fee per event; the vendor's share taken back from its
    transfer), except the commission: it is `commission_back`, the recompute's delta under the
    frozen rule (owner decision on #564). Submitted to the provider in this step, `due_by` = the
    event plus 14 days. An amendment the customer asked for refunds without the refund fee
    (exclusion `customer_requested_amendment`, set here from the amendment, never by hand,
    audited). The units the amendment takes out are stored with the request, so a later refund
    of the same line cannot give them back twice."""
    sub = _lock(amendment.vendor_order)
    _check_paid(sub)
    lines = _units_by_line(sub, _removed_units(amendment))
    _check_not_refunded(sub, amendment)
    asked = _customer_asked(amendment)
    plan = _Plan(
        gross=round_line(amount),
        request={
            "mode": CAUSE_AMENDMENT,
            "amendment": str(amendment.public_id),
            "amount": str(round_line(amount)),
            "lines": [{"line": line_id, "qty": qty} for line_id, qty in lines],
        },
        channel=RefundChannel.THROUGH_PLATFORM,
        cause=CAUSE_AMENDMENT,
        key=f"refund:{sub.public_id}:amendment:{amendment.public_id}",
        reason=f"Order amendment {amendment.public_id}",
        exclusion=(
            CustomerRequestedAmendment(
                amendment=str(amendment.public_id), set_by="service:orders.amendments"
            )
            if asked
            else None
        ),
        amendment=amendment,
        due_by=now + dt.timedelta(days=REFUND_DUE_DAYS),
        commission_back=round_line(commission_back),
    )
    try:
        return _outcome(_carry_out(sub, plan, ctx=ctx, dry_run=dry_run, now=now, defer=True))
    except RefundFailed as error:
        raise _provider_failed(error) from error
    except RefundRefused as error:
        raise orders_amendments.AmendmentRefused(
            f"The refund of this change cannot be made: {error}."
        ) from error


def refund_rest_of_package(
    vendor_order: VendorOrder,
    *,
    ctx: AuditContext,
    now: dt.datetime,
    dry_run: bool = False,
) -> "orders_amendments.RefundOutcome":
    """The customer cancels the rest of the package after a partial unavailability (ORD.20):
    a full refund of what is left under PAY.4, refund fee charged to the vendor (D-05), due
    within 14 days. Called by `apps.orders.amendments.cancel_rest_of_package` (setting
    `ORDER_CANCEL_REST_REFUND_HOOK`); the package becomes cancelled or refunded."""
    sub = _lock(vendor_order)
    _check_paid(sub)
    left = round_line(sub.gross_total - _sum(_group(sub, MOVED), "refunded_gross"))
    if left <= 0:
        raise orders_amendments.AmendmentRefused("Nothing is left to refund on this package.")
    already = _already(sub)
    units = [
        (line_id, figures.quantity - already.get(line_id, 0))
        for line_id, figures in sorted(_line_figures(sub).items())
        if figures.quantity - already.get(line_id, 0) > 0
    ]
    plan = _Plan(
        gross=left,
        request={
            "mode": CAUSE_CANCEL_REST,
            "amount": str(left),
            "lines": [{"line": line_id, "qty": qty} for line_id, qty in units],
        },
        channel=RefundChannel.THROUGH_PLATFORM,
        cause=CAUSE_CANCEL_REST,
        key=f"refund:{sub.public_id}:cancel-rest",
        reason="The customer cancelled the rest of the package after an item was unavailable",
        due_by=now + dt.timedelta(days=REFUND_DUE_DAYS),
    )
    try:
        return _outcome(_carry_out(sub, plan, ctx=ctx, dry_run=dry_run, now=now, defer=True))
    except RefundFailed as error:
        raise _provider_failed(error) from error
    except RefundRefused as error:
        raise orders_amendments.AmendmentRefused(
            f"The rest of the package cannot be refunded: {error}."
        ) from error


def alert_refunds_near_due(*, now: dt.datetime | None = None) -> int:
    """Alert staff for each refund with a 14-day `due_by` that the provider has not confirmed
    4 days before it (ORD.20). Returns how many are alerted; `raise_alert` sends the email once
    per refund and repeat window."""
    moment = now or timezone.now()
    late = Refund.objects.filter(
        due_by__isnull=False,
        succeeded_at__isnull=True,
        due_by__lte=moment + dt.timedelta(days=REFUND_ALERT_DAYS_BEFORE),
    ).select_related("vendor_order__order")
    count = 0
    for refund in late:
        raise_alert(
            Alert(
                f"refund-due:{refund.public_id}",
                "A refund is not confirmed by the payment provider 4 days before its 14-day "
                "deadline",
                {
                    "refund": str(refund.public_id),
                    "order": refund.vendor_order.order.number,
                    "due_by": refund.due_by.isoformat() if refund.due_by else "",
                    "provider_status": refund.provider_status,
                },
            ),
            now=moment,
        )
        count += 1
    return count


def _apply_states(refund: Refund, ctx: AuditContext) -> bool:
    """Roll the order states forward for a recorded refund, in a savepoint. The buyer is already
    refunded at the provider, so a failure here (a deadlock, a state the machine refuses) must
    not roll back the Refund and its ledger lines: the refund is kept with `states_pending`,
    audited and alerted once, and the sweep or a replay of the same request finishes it. True
    when the states were applied."""
    sub = refund.vendor_order
    done = _sum(_group(sub, MOVED), "refunded_gross") >= sub.gross_total
    try:
        with transaction.atomic():
            orders_services.apply_refund_to_states(sub, fully_refunded=done, ctx=ctx)
    except Exception:
        if not refund.states_pending:
            refund.states_pending = True
            refund.save(update_fields=["states_pending"])
            flagged = ChangeSet()
            flagged.create(
                REFUND_MODEL,
                str(refund.public_id),
                {
                    VENDOR_FIELD: str(refund.vendor.public_id),
                    "provider_refund_ref": refund.provider_refund_ref,
                },
            )
            audit_changes(flagged, ctx=ctx, action=REFUND_STATES_PENDING)
            raise_alert(
                Alert(
                    f"refund-states:{refund.public_id}",
                    "A refund went through but the order states were not updated; it is retried",
                    {"refund": str(refund.public_id), "order": sub.order.number},
                )
            )
        return False
    if refund.states_pending:
        refund.states_pending = False
        refund.save(update_fields=["states_pending"])
    return True


def _flag_pending_reversal(refund: Refund, ctx: AuditContext) -> None:
    """Audit and alert staff: the buyer is refunded but the vendor's share is not yet taken back."""
    flagged = ChangeSet()
    flagged.create(
        REFUND_MODEL,
        str(refund.public_id),
        {
            VENDOR_FIELD: str(refund.vendor.public_id),
            "provider_refund_ref": refund.provider_refund_ref,
            "transfer_reversed": refund.transfer_reversed,
        },
    )
    audit_changes(flagged, ctx=ctx, action=REFUND_REVERSAL_PENDING)
    raise_alert(
        Alert(
            f"refund-reversal:{refund.public_id}",
            "A refund went through but the vendor's transfer reversal failed; it is retried",
            {"refund": str(refund.public_id), "order": refund.vendor_order.order.number},
        )
    )


def _transfer_ref(sub: VendorOrder) -> str:
    entry = LedgerEntry.objects.filter(idempotency_key=settlement.key(sub, engine.TRANSFER)).first()
    return entry.external_ref if entry is not None else ""


def _finish_pending(key: str) -> bool:
    """Finish what the refund with this key left pending after the provider refund: the
    transfer reversal, then the order states. The provider refund is never called again. Locks
    Order, then VendorOrder, then the Refund row (the refund path's one lock order). True when
    nothing is left pending."""
    found = (
        Refund.objects.filter(idempotency_key=key)
        .filter(Q(reversal_pending=True) | Q(states_pending=True))
        .values_list("vendor_order_id", flat=True)
        .first()
    )
    if found is None:
        return True
    _lock(VendorOrder.objects.select_related("order").get(pk=found))
    refund = (
        Refund.objects.select_for_update(of=("self",))
        .select_related("vendor", "vendor_order__order")
        .get(idempotency_key=key)
    )
    ctx = AuditContext(actor_label="system:refund-reversal", source=AuditLog.Source.SYSTEM)
    reversed_ok = not refund.reversal_pending or _finish_pending_reversal(refund, ctx)
    states_ok = not refund.states_pending or _apply_states(refund, ctx)
    return reversed_ok and states_ok


def _finish_pending_reversal(refund: Refund, ctx: AuditContext) -> bool:
    """Retry only the transfer reversal of this refund, under the same provider key
    `<key>:reverse:<transfer>`, so a reversal that reached the provider but whose answer was lost
    is replayed, never made twice. True when done."""
    key = refund.idempotency_key
    sub = refund.vendor_order
    transfer = _transfer_ref(sub)
    try:
        payment = _payment_of(sub.order)
        refs = registry.get_provider(payment.provider).reverse_transfers(
            idempotency_key=key,
            currency=payment.currency,
            reversals=((TransferReversal(transfer, refund.transfer_reversed),))
            if transfer and refund.transfer_reversed > 0
            else (),
        )
    except (ProviderError, registry.UnknownProvider, RefundRefused):
        _flag_pending_reversal(refund, ctx)
        return False
    refund.reversal_pending = False
    refund.save(update_fields=["reversal_pending"])
    done = ChangeSet()
    done.create(
        REFUND_MODEL,
        str(refund.public_id),
        {VENDOR_FIELD: str(refund.vendor.public_id), "reversal_refs": list(refs)},
    )
    audit_changes(done, ctx=ctx, action=REFUND_REVERSAL_DONE)
    return True


def retry_pending_reversals() -> int:
    """The sweep: finish each refund left pending after the provider refund (its transfer
    reversal, its order states); returns how many cleared."""
    keys = list(
        Refund.objects.filter(Q(reversal_pending=True) | Q(states_pending=True)).values_list(
            "idempotency_key", flat=True
        )
    )
    cleared = 0
    for key in keys:
        with transaction.atomic():
            cleared += _finish_pending(key)
    return cleared


@dataclass(frozen=True)
class _Figures:
    commission_reversal: Decimal
    payment_fee_reversal: Decimal
    refund_fee: Decimal


@dataclass(frozen=True)
class _Transfer:
    ref: str
    remaining: Decimal


def _figures(
    sub: VendorOrder,
    snapshot: dict[str, object],
    gross: Decimal,
    provider_cost: Decimal,
    rule: RefundFeeRule,
    exclusion: FeeExclusion | None,
    reverse: bool,
    commission_back: Decimal | None = None,
    exclude: int | None = None,
) -> _Figures:
    """`refund_lines` for this event, the reversals capped so that all the events of the package
    together never give back more than the commission and collection cost it paid. An amendment
    passes `commission_back` (its recompute's commission delta) in place of the pro rata. The
    event that refunds the rest of the package gives back all that is left of both."""
    str_snapshot = {k: str(v) for k, v in snapshot.items()}
    moved = _group(sub, MOVED).exclude(pk=exclude) if exclude else _group(sub, MOVED)
    lines = refund_lines(
        str_snapshot,
        RefundEvent(gross, provider_cost, exclusion),
        goods_net=sub.goods_net,
        gross_paid=sub.gross_total,
        refund_fee_rule=rule,
        shipping_net=sub.shipping_net,
        refunded_before=_sum(moved, "refunded_gross"),  # cumulative: the last takes the rest
    )
    paid = compute_commission(
        str_snapshot,
        goods_net=sub.goods_net,
        gross_paid=sub.gross_total,
        shipping_net=sub.shipping_net,
    )
    commission_left = max(ZERO, paid.commission - _sum(moved, "commission_reversal"))
    fee_left = max(ZERO, paid.payment_fee - _sum(moved, "payment_fee_reversal"))
    # Lane-a decision on #564: an amendment's commission delta is a price correction, given
    # back whatever `reverse_fees_on_vendor_refund` says.
    amended = None if commission_back is None else min(commission_back, commission_left)
    if not reverse:
        return _Figures(amended if amended is not None else ZERO, ZERO, lines.refund_fee)
    if round_line(_sum(moved, "refunded_gross") + gross) >= sub.gross_total:
        return _Figures(commission_left, fee_left, lines.refund_fee)
    return _Figures(
        amended if amended is not None else min(lines.commission_reversal, commission_left),
        min(lines.payment_fee_reversal, fee_left),
        lines.refund_fee,
    )


def _transfer_of(sub: VendorOrder) -> _Transfer | None:
    """The vendor's transfer of this package and what the provider can still take back from it,
    or None when no transfer was made (it is pending, or never was due): then there is nothing to
    reverse and the pending transfer is reduced instead (`settlement.transferable_net`)."""
    entry = LedgerEntry.objects.filter(idempotency_key=settlement.key(sub, engine.TRANSFER)).first()
    if entry is None or not entry.external_ref:
        return None
    reversed_so_far = _sum(
        LedgerEntry.objects.filter(vendor_order=sub, kind=engine.TRANSFER_REVERSAL), "amount"
    )
    return _Transfer(entry.external_ref, round_line(-entry.amount - reversed_so_far))


def _reversal_amount(planned: _Figures, gross: Decimal, transfer: _Transfer | None) -> Decimal:
    """What the reverse transfer takes back: the vendor's share of the refunded gross (gross minus
    the reversed commission and collection cost), never more than the transfer still holds."""
    if transfer is None:
        return ZERO
    share = round_line(gross - planned.commission_reversal - planned.payment_fee_reversal)
    return max(ZERO, min(share, transfer.remaining))


def _write_lines(
    sub: VendorOrder,
    refund: Refund,
    refund_ref: str,
    reversal_refs: Sequence[str],
    transfer: _Transfer | None,
    ctx: AuditContext,
) -> None:
    rows: list[tuple[str, Decimal, str, str]] = [
        (engine.REFUND, -refund.refunded_gross, "refund to the buyer", refund_ref),
        (
            engine.COMMISSION_REVERSAL,
            refund.commission_reversal,
            "commission given back on the refunded share",
            "",
        ),
        (
            engine.PAYMENT_FEE_REVERSAL,
            refund.payment_fee_reversal,
            "collection cost given back on the refunded share",
            "",
        ),
        (
            engine.REFUND_FEE,
            -refund.refund_fee,
            "refund fee: provider cost plus surcharge, rounded up (charged to the vendor)",
            "",
        ),
        (
            engine.TRANSFER_REVERSAL,
            refund.transfer_reversed,
            "vendor share taken back from its transfer",
            reversal_refs[0] if reversal_refs else "",
        ),
    ]
    suffix = f" (amendment {refund.amendment.public_id})" if refund.amendment is not None else ""
    for kind, amount, memo, external in rows:
        if amount == 0:
            continue
        memo = f"{memo}{suffix}" if suffix else memo
        services.record_entry(
            sub.vendor,
            kind=kind,
            amount=amount,
            idempotency_key=f"refund:{refund.public_id}:{kind}",
            ctx=ctx,
            vendor_order=sub,
            source_ref=sub.order.number,
            external_ref=external,
            gateway_account=payments_settlement.ready_account_ref(sub)
            if kind == engine.TRANSFER_REVERSAL
            else "",
            memo=memo,
        )


def mark_refunded_outside(
    vendor_order: VendorOrder,
    selection: RefundSelection,
    *,
    request_id: str,
    actor: object,
    ctx: AuditContext,
    declared_date: dt.date,
    declared_method: str,
    reason: str = "",
    dry_run: bool = False,
) -> RefundChangeSet:
    """The vendor marks that it refunded the buyer OUTSIDE the platform (D-41 channel 2).

    Moves nothing: no ledger line, no provider call, no state change, no change of what the
    platform reports for DAC7. Requires the declared refund date (not in the future) and method;
    refused when the operator setting `vendors_may_refund_outside` is off. The platform is not
    liable for the refund; the audit entry says so. The `orders` action on the vendor is needed."""
    if not has_vendor_permission(actor, vendor_order.vendor, "orders"):  # type: ignore[arg-type]
        raise PermissionDenied("Only a member of the vendor can mark its refunds.")
    _check_request_id(request_id)
    method = declared_method.strip()
    if not method or len(method) > METHOD_MAX:
        raise RefundRefused("the refund method is required (at most 64 characters)")
    if declared_date > timezone.localdate():
        raise RefundRefused("the refund date cannot be in the future")
    if len(reason) > REASON_MAX:
        raise RefundRefused("the reason is too long")
    key = f"refund-outside:{vendor_order.public_id}:{request_id}"
    result = RefundChangeSet()
    with transaction.atomic():
        sub = _lock(vendor_order)
        _check_paid(sub)
        if not current_operator().vendors_may_refund_outside:
            raise RefundRefused("this platform does not allow refunds outside the platform")
        if _replay(sub, key, RefundChannel.OUTSIDE_PLATFORM, selection):
            return result
        outside = (RefundChannel.OUTSIDE_PLATFORM,)
        gross = _gross_of(sub, selection, outside)
        marked = _sum(_group(sub, outside), "refunded_gross")
        if gross > round_line(sub.gross_total - marked):
            raise RefundRefused("the mark exceeds what is left of the vendor order")
        refund = Refund(
            vendor=sub.vendor,
            vendor_order=sub,
            channel=RefundChannel.OUTSIDE_PLATFORM,
            idempotency_key=key,
            request=_stored(sub, selection, outside).snapshot(),
            refunded_gross=gross,
            declared_date=declared_date,
            declared_method=method,
            reason=reason,
            created_by=ctx.actor_label[:128],
        )
        result.create(
            REFUND_MODEL,
            str(refund.public_id),
            _created(
                refund,
                {
                    "platform_liable": False,
                    "ledger_movement": False,
                    "dac7_consideration_changed": False,
                },
            ),
        )
        if dry_run:
            return result
        refund.save()
        orders_amendments.supersede_open_amendment(sub, cause="refund", ctx=ctx)
        audit_changes(result, ctx=ctx, action=REFUND_MARKED_OUTSIDE)
        result.refund = refund
    return result


__all__ = [
    "InvalidRefundRequest",
    "RefundChangeSet",
    "RefundFailed",
    "RefundRefused",
    "RefundSelection",
    "StaffRequired",
    "alert_refunds_near_due",
    "mark_refunded_outside",
    "refund_amendment",
    "refund_by_platform",
    "refund_rest_of_package",
    "refund_through_platform",
    "retry_pending_reversals",
    "submit_pending_refund",
    "submit_pending_refunds",
]

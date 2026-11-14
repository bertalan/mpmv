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

Safety: the vendor order row is locked for the whole event; the provider call carries a key built
from the caller's `request_id`, so a repeat after a lost answer replays the same refund and the
ledger keys make the lines idempotent. The provider is called inside the transaction on purpose
(like the transfer): a failure raises `RefundFailed` and nothing is written.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone

from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import PLATFORM_ERROR, PLATFORM_REFUND_CASES, PlatformOperator
from apps.core.money import round_line
from apps.core.services import audit_changes, current_operator
from apps.orders import services as orders_services
from apps.orders.models import Order, OrderLine, VendorOrder
from apps.payments import registry
from apps.payments import settlement as payments_settlement
from apps.payments.engine import ProviderError, RefundRequest, TransferReversal
from apps.payments.models import Payment
from apps.vendors import engine as vendor_engine
from apps.vendors.commission import RefundEvent, compute_commission, refund_lines
from apps.vendors.refund_fee import PlatformErrorExclusion, RefundFeeRule
from apps.vendors.services import has_vendor_permission

from . import engine, refund_engine, services, settlement
from .models import LedgerEntry, Refund, RefundChannel
from .refund_engine import InvalidRefundRequest, LineFigures, RefundSelection

REFUND_MODEL = "ledger.refund"
REFUND_RECORDED = "ledger.refund_recorded"
REFUND_MARKED_OUTSIDE = "ledger.refund_marked_outside"
REFUND_FEE_EXCLUDED = "ledger.refund_fee_excluded"
ZERO = Decimal("0.00")
MOVED = (RefundChannel.THROUGH_PLATFORM, RefundChannel.PLATFORM_INITIATED)
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.-]{8,64}")
METHOD_MAX = 64
REASON_MAX = 2000
SNAPSHOT_SURCHARGE_KEY = "refund_fee_surcharge"


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
    return (
        VendorOrder.objects.select_for_update()
        .select_related("order", "vendor")
        .get(pk=vendor_order.pk)
    )


def _check_paid(sub: VendorOrder) -> None:
    if sub.order.state not in orders_services.PAID_OR_LATER:
        raise RefundRefused(f"the order is {sub.order.state}: only a paid order is refunded")


def _line_figures(sub: VendorOrder) -> dict[int, LineFigures]:
    return {
        line.pk: LineFigures(line.pk, line.quantity, round_line(line.net_total + line.tax_amount))
        for line in OrderLine.objects.filter(vendor_order=sub)
    }


def _group(sub: VendorOrder, channels: tuple[str, ...]) -> "QuerySet[Refund]":
    return Refund.objects.filter(vendor_order=sub, channel__in=channels)


def _sum(rows: "QuerySet[Any]", field: str) -> Decimal:
    return round_line(rows.aggregate(total=Sum(field))["total"] or ZERO)


def _gross_of(sub: VendorOrder, selection: RefundSelection, channels: tuple[str, ...]) -> Decimal:
    rows = _group(sub, channels)
    already = refund_engine.refunded_quantities(rows.values_list("request", flat=True))
    return refund_engine.refunded_gross_of(selection, _line_figures(sub), already)


def _stored(
    sub: VendorOrder, selection: RefundSelection, channels: tuple[str, ...]
) -> RefundSelection:
    rows = _group(sub, channels)
    already = refund_engine.refunded_quantities(rows.values_list("request", flat=True))
    return refund_engine.normalised_lines(selection, _line_figures(sub), already)


def _fee_rule(snapshot: dict[str, object], operator: PlatformOperator) -> RefundFeeRule:
    """The plan's surcharge when the order's frozen rule carries one, else the operator's default
    read now (ARC.7: the version in force at refund time)."""
    if SNAPSHOT_SURCHARGE_KEY in snapshot:
        return RefundFeeRule.from_snapshot(
            {SNAPSHOT_SURCHARGE_KEY: str(snapshot[SNAPSHOT_SURCHARGE_KEY])}
        )
    return RefundFeeRule(surcharge=round_line(operator.refund_fee_surcharge))


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
    result = RefundChangeSet()
    with transaction.atomic():
        sub = _lock(vendor_order)
        _check_paid(sub)
        if sub.state in {"cancelled", "refunded"}:
            raise RefundRefused(f"the vendor order is {sub.state}: nothing is left to refund")
        if _replay(sub, key, channel, selection):
            return result
        operator = current_operator()
        gross = _gross_of(sub, selection, MOVED)
        refunded_before = _sum(_group(sub, MOVED), "refunded_gross")
        if gross > round_line(sub.gross_total - refunded_before):
            raise RefundRefused("the refund exceeds what is left of the vendor order")
        payment = _payment_of(sub.order)
        snapshot = _snapshot_of(sub)
        rule = _fee_rule(snapshot, operator)
        reverse = (
            channel == RefundChannel.PLATFORM_INITIATED or operator.reverse_fees_on_vendor_refund
        )
        exclusion = (
            PlatformErrorExclusion(reason=exclusion_reason, set_by=ctx.actor_label)
            if exclusion_reason
            else None
        )

        def lines_for(cost: Decimal) -> _Figures:
            return _figures(sub, snapshot, gross, cost, rule, exclusion, reverse)

        planned = lines_for(ZERO)
        transfer = _transfer_of(sub)
        reversal_amount = _reversal_amount(planned, gross, transfer)
        refund = Refund(
            vendor=sub.vendor,
            vendor_order=sub,
            channel=channel,
            cause=cause,
            idempotency_key=key,
            request=_stored(sub, selection, MOVED).snapshot(),
            refunded_gross=gross,
            platform_error=exclusion is not None,
            platform_error_reason=exclusion_reason,
            reason=reason,
            created_by=ctx.actor_label[:128],
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
        try:
            answer = registry.get_provider(payment.provider).refund(
                RefundRequest(
                    provider_ref=payment.provider_ref,
                    amount=gross,
                    currency=payment.currency,
                    idempotency_key=key,
                    reverse_vendor_shares=False,
                    reversals=(
                        (TransferReversal(transfer.ref, reversal_amount),)
                        if transfer is not None and reversal_amount > 0
                        else ()
                    ),
                )
            )
        except (ProviderError, registry.UnknownProvider) as error:
            raise RefundFailed(str(error)) from error
        final = lines_for(round_line(answer.provider_cost))
        refund.commission_reversal = final.commission_reversal
        refund.payment_fee_reversal = final.payment_fee_reversal
        refund.refund_fee = final.refund_fee
        refund.provider_cost = round_line(answer.provider_cost)
        refund.provider_refund_ref = answer.refund_ref
        refund.transfer_reversed = reversal_amount
        refund.save()
        _write_lines(sub, refund, answer.refund_ref, answer.reversal_refs, transfer, ctx)
        changes = ChangeSet()
        changes.create(REFUND_MODEL, str(refund.public_id), _created(refund))
        audit_changes(changes, ctx=ctx, action=REFUND_RECORDED)
        if exclusion is not None:
            flagged = ChangeSet()
            flagged.create(
                REFUND_MODEL,
                str(refund.public_id),
                {
                    VENDOR_FIELD: str(sub.vendor.public_id),
                    "reason": exclusion.reason,
                    "set_by": exclusion.set_by,
                },
            )
            audit_changes(flagged, ctx=ctx, action=REFUND_FEE_EXCLUDED)
        done = _sum(_group(sub, MOVED), "refunded_gross") >= sub.gross_total
        orders_services.apply_refund_to_states(sub, fully_refunded=done, ctx=ctx)
        result.changes = changes.changes
        result.refund = refund
    return result


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
    exclusion: PlatformErrorExclusion | None,
    reverse: bool,
) -> _Figures:
    """`refund_lines` for this event, the reversals capped so that all the events of the package
    together never give back more than the commission and collection cost it paid."""
    str_snapshot = {k: str(v) for k, v in snapshot.items()}
    lines = refund_lines(
        str_snapshot,
        RefundEvent(gross, provider_cost, exclusion),
        goods_net=sub.goods_net,
        gross_paid=sub.gross_total,
        refund_fee_rule=rule,
        shipping_net=sub.shipping_net,
    )
    if not reverse:
        return _Figures(ZERO, ZERO, lines.refund_fee)
    paid = compute_commission(
        str_snapshot,
        goods_net=sub.goods_net,
        gross_paid=sub.gross_total,
        shipping_net=sub.shipping_net,
    )
    moved = _group(sub, MOVED)
    commission_left = max(ZERO, paid.commission - _sum(moved, "commission_reversal"))
    fee_left = max(ZERO, paid.payment_fee - _sum(moved, "payment_fee_reversal"))
    return _Figures(
        min(lines.commission_reversal, commission_left),
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
    for kind, amount, memo, external in rows:
        if amount == 0:
            continue
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
    "mark_refunded_outside",
    "refund_by_platform",
    "refund_through_platform",
]

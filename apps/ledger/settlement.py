"""Ledger lines and vendor transfers of a paid order (PAY.3, owner decision D-08).

Flow, when a payment moves its order to `paid` (`on_order_paid`, same transaction): for EACH
vendor package the paid-time figures (`apps.payments.settlement`, from the order's frozen
commission snapshot) become append-only ledger lines (`sale`, `shipping`, `commission`,
`payment_fee`), then a queued job sends ONE transfer per vendor package through the provider
interface (`source_transaction` = the order's charge, `transfer_group` = the order number) and
records it as a `transfer` line. After that, `sale + shipping - commission - payment_fee - transfer`
is zero for the package.

Rules that keep money safe:
- Ledger lines never wait for the transfer: they are written in the payment's transaction, so the
  order stays paid and the vendor's balance is right even when Stripe is down.
- Every step is idempotent: ledger keys are per vendor order and kind, the transfer key is per order
  and vendor (also sent to Stripe), so a redelivery or a retry never writes or pays twice.
- A failed transfer is audited and left for the next sweep (`transfer_pending_payments`); nothing
  is rolled back.
- A vendor without a ready payout account, or whose snapshot cannot be used, gets no transfer. The
  first case keeps its ledger lines, so its balance is the receivable the platform owes it; the
  transfer follows when the account is ready. The second is audited and written nowhere (never a
  silent 0% commission) until the snapshot is fixed. The money stays on the platform either way.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone

from apps.core.audit import VENDOR_FIELD, AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import NoPlatformOperator, audit_changes, current_operator
from apps.orders.models import Order, VendorOrder
from apps.orders.services import PAID_OR_LATER
from apps.payments import registry, settlement
from apps.payments.engine import PaymentProvider, ProviderError, TransferRequest
from apps.payments.models import Payment
from apps.vendors.engine import NoCommissionRule

from . import engine, services
from .models import LedgerEntry, Refund, RefundChannel

logger = logging.getLogger(__name__)

SETTLEMENT_ACTOR = "system:order-settlement"
PROBLEM_MODEL = "ledger.settlement"
POSTING_BLOCKED = "ledger.posting_blocked"
SETTLEMENT_BLOCKED_VAT_MODE = "ledger.settlement_blocked_vat_mode"
TRANSFER_FAILED = "ledger.transfer_failed"
TRANSFER_WITHOUT_ACCOUNT = "ledger.transfer_without_account"
TRANSFER_BLOCKED = "ledger.transfer_blocked"
TRANSFER_SKIPPED_REFUNDED = "ledger.transfer_skipped_refunded"
# Order or package states from which no transfer may leave. `partially_refunded` blocks too,
# unless the platform itself recorded the refund (`recorded_refunded`, PAY.4): a refund through
# the platform is a `Refund` row with ledger lines per vendor package, so a pending transfer is
# reduced by it, or dropped when nothing is left (`transferable_net`). A partial refund nobody
# recorded (a state set by hand, the Stripe dashboard) keeps blocking everything.
# (A paid order cannot be cancelled: the order table refuses it, and `paid_order` excludes
# cancelled orders anyway. The state stays listed as a second line of defence.)
_NO_TRANSFER_ORDER_STATES = frozenset({"cancelled", "refunded", "partially_refunded"})
_NO_TRANSFER_PACKAGE_STATES = frozenset({"cancelled", "refunded"})
DEFAULT_LOOKBACK_DAYS = 30
CURRENCY = engine.CURRENCY

_POSTING_ERRORS = (engine.LedgerError, ValueError, NoCommissionRule, NoPlatformOperator)


def system_ctx() -> AuditContext:
    return AuditContext(actor_label=SETTLEMENT_ACTOR, source=AuditLog.Source.SYSTEM)


@dataclass
class Outcome:
    """What a call did, for the sweep log and the tests."""

    posted: int = 0  # ledger entries written now
    transfers: list[str] = field(default_factory=list)  # provider transfer ids created or found
    failed: int = 0  # transfers the provider refused
    skipped: int = 0  # vendors without a ready account (receivable kept)
    refunded: int = 0  # vendor orders not paid because of a refund or a cancellation (audited)
    blocked: int = 0  # vendor orders that cannot be settled (audited)


def key(vendor_order: VendorOrder, kind: str) -> str:
    return f"order-paid:{vendor_order.public_id}:{kind}"


def _problem(
    action: str, vendor_order: VendorOrder, detail: dict[str, object], ctx: AuditContext
) -> None:
    changes = ChangeSet()
    changes.create(
        PROBLEM_MODEL,
        str(vendor_order.public_id),
        {VENDOR_FIELD: str(vendor_order.vendor.public_id), "order": vendor_order.order.number}
        | detail,
    )
    audit_changes(changes, ctx=ctx, action=action)


def _problem_once(
    action: str, vendor_order: VendorOrder, detail: dict[str, object], ctx: AuditContext
) -> None:
    """Audit a standing problem once per vendor order and reason: the sweep sees it again every
    run and must not flood the trail."""
    known = AuditLog.objects.filter(
        action=action, target_key=str(vendor_order.public_id), after__reason=detail.get("reason")
    )
    if not known.exists():
        _problem(action, vendor_order, detail, ctx)


def paid_order(payment: Payment) -> Order | None:
    """The paid order this payment settles, or None (unknown order, not paid, other amount, or a
    duplicate payment: PAY.4 refunds it in full and nothing is posted or transferred from it)."""
    if payment.duplicate:
        return None
    order = Order.objects.filter(number=payment.order_ref).first()
    if order is None or order.state not in PAID_OR_LATER:
        return None
    if payment.currency.upper() != CURRENCY or payment.amount != order.grand_total:
        return None
    return order


def vat_mode_blocks(order: Order, ctx: AuditContext) -> bool:
    """True when the platform's `commission_vat_mode` is not "none": spec 04 section 4.4 case 3
    ("No VAT on the commission and no VAT on the payment fee"), the only regime the figures
    support. Any other mode needs VAT on the commission and fee (VEN.34), which nothing applies
    yet, so nothing is posted or transferred: the order stays paid and the money stays on the
    platform. Audited once per order."""
    try:
        mode = current_operator().commission_vat_mode
    except NoPlatformOperator:
        return False  # the per-entry handling reports a missing operator
    if mode == PlatformOperator.CommissionVatMode.NONE:
        return False
    already = AuditLog.objects.filter(
        action=SETTLEMENT_BLOCKED_VAT_MODE, target_key=order.number
    ).exists()
    if not already:
        changes = ChangeSet()
        changes.create(PROBLEM_MODEL, order.number, {"commission_vat_mode": mode})
        audit_changes(changes, ctx=ctx, action=SETTLEMENT_BLOCKED_VAT_MODE)
    return True


def post_payment(payment: Payment, *, ctx: AuditContext | None = None) -> Outcome:
    """Write the ledger lines of every vendor package of the order `payment` paid. Idempotent."""
    ctx = ctx or system_ctx()
    outcome = Outcome()
    order = paid_order(payment)
    if order is None:
        return outcome
    if vat_mode_blocks(order, ctx):
        outcome.blocked += 1
        return outcome
    plan = settlement.order_settlement(order)
    for blocked in plan.blocked:
        outcome.blocked += 1
        _problem_once(POSTING_BLOCKED, blocked.vendor_order, {"reason": blocked.reason}, ctx)
    for line in plan.vendors:
        vendor_order = line.vendor_order
        try:
            with transaction.atomic():
                outcome.posted += _post_vendor(line, order, ctx)
        except _POSTING_ERRORS as error:
            outcome.blocked += 1
            _problem_once(POSTING_BLOCKED, vendor_order, {"reason": str(error)}, ctx)
            logger.warning("ledger posting blocked for %s: %s", vendor_order.public_id, error)
    return outcome


def _post_vendor(line: settlement.VendorSettlement, order: Order, ctx: AuditContext) -> int:
    vendor_order = line.vendor_order
    rows: list[tuple[str, Decimal, str]] = [
        (engine.SALE, line.sale, "goods with VAT collected for the vendor"),
        (engine.SHIPPING, line.shipping, "shipping with VAT collected for the vendor"),
        (engine.COMMISSION, -line.commission, "commission on the order snapshot"),
        (engine.PAYMENT_FEE, -line.payment_fee, "payment fee on the gross paid"),
    ]
    written = 0
    for kind, amount, memo in rows:
        if amount == 0:
            continue
        changes = services.record_entry(
            vendor_order.vendor,
            kind=kind,
            amount=amount,
            idempotency_key=key(vendor_order, kind),
            ctx=ctx,
            vendor_order=vendor_order,
            source_ref=order.number,
            memo=memo,
        )
        written += len(changes.changes)
    return written


def transfer_payment(payment: Payment, *, ctx: AuditContext | None = None) -> Outcome:
    """Send the one transfer of each vendor package that has its ledger lines and no transfer yet.

    A provider failure is audited and counted, never raised: the next call retries it. Ledger
    lines are written first when missing, so a transfer never has a package without them."""
    ctx = ctx or system_ctx()
    outcome = post_payment(payment, ctx=ctx)
    order = paid_order(payment)
    provider = registry.configured_provider()
    if order is None or provider is None or vat_mode_blocks(order, ctx):
        return outcome  # an unpaid or cancelled order (`paid_order`) is never transferred
    plan = settlement.order_settlement(order)
    paid_total = sum((line.vendor_net for line in plan.vendors if line.transferable), Decimal("0"))
    charge_check = _ChargeCheck()
    for line in plan.vendors:
        # The vendor order row is locked while its transfer is decided and sent: a refund through
        # the platform (`apps.ledger.refunds`) takes the same lock, so a transfer and a refund of
        # one package never interleave (PAY.4).
        with transaction.atomic():
            VendorOrder.objects.select_for_update().filter(pk=line.vendor_order.pk).first()
            _transfer_vendor(provider, payment, order, line, paid_total, charge_check, ctx, outcome)
    return outcome


def _transfer_vendor(
    provider: PaymentProvider,
    payment: Payment,
    order: Order,
    line: settlement.VendorSettlement,
    paid_total: Decimal,
    charge_check: "_ChargeCheck",
    ctx: AuditContext,
    outcome: Outcome,
) -> None:
    vendor_order = line.vendor_order
    vendor_order.refresh_from_db(fields=["state"])
    if LedgerEntry.objects.filter(idempotency_key=key(vendor_order, engine.TRANSFER)).exists():
        return
    if not LedgerEntry.objects.filter(idempotency_key=key(vendor_order, engine.SALE)).exists():
        return  # not posted (blocked above): never transfer without ledger lines
    if line.vendor_net <= 0:
        return
    reason = _refund_reason(provider, payment, order, vendor_order, charge_check)
    if reason:
        outcome.refunded += 1
        _problem_once(TRANSFER_SKIPPED_REFUNDED, vendor_order, {"reason": reason}, ctx)
        return
    amount = transferable_net(vendor_order, line.vendor_net)
    if amount <= 0:  # refunded before the transfer: nothing is left to send (PAY.4)
        outcome.refunded += 1
        _problem_once(
            TRANSFER_SKIPPED_REFUNDED, vendor_order, {"reason": "refunded before the transfer"}, ctx
        )
        return
    if not line.account_ref:
        outcome.skipped += 1
        _noted_without_account(vendor_order, line, ctx)
        return
    if paid_total > payment.amount:
        outcome.blocked += 1
        _problem_once(
            TRANSFER_BLOCKED,
            vendor_order,
            {"reason": "the vendor shares exceed the payment", "payment": payment.amount},
            ctx,
        )
        return
    _send(provider, payment, order, line, amount, ctx, outcome)


# Ledger kinds that move money to or from the vendor's account: not part of what is still owed.
_TRANSFER_KINDS = (engine.TRANSFER, engine.TRANSFER_REVERSAL)


def moved_refunds(vendor_order: VendorOrder) -> "QuerySet[Refund]":
    """The refunds of a package that moved money (through the platform, or by the platform);
    the vendor's outside-the-platform marks move nothing and are not here."""
    return Refund.objects.filter(
        vendor_order=vendor_order,
        channel__in=[RefundChannel.THROUGH_PLATFORM, RefundChannel.PLATFORM_INITIATED],
    )


def transferable_net(vendor_order: VendorOrder, vendor_net: Decimal) -> Decimal:
    """What a still-pending transfer may send (PAY.4): the paid-time `vendor_net`, reduced by
    every refund of the package (the refund line, the reversed commission and collection cost and
    the refund fee are all ledger lines). Without a refund it is `vendor_net`. Zero or less means
    the refund took it all: no transfer is made, and no reverse transfer follows for a transfer
    that never existed."""
    if not moved_refunds(vendor_order).exists():
        return vendor_net
    owed = (
        LedgerEntry.objects.filter(vendor_order=vendor_order)
        .exclude(kind__in=_TRANSFER_KINDS)
        .aggregate(total=Sum("amount"))["total"]
    )
    return engine.money(owed or Decimal("0"))


class _ChargeCheck:
    """The provider's refunded amount for one payment, read at most once per call."""

    def __init__(self) -> None:
        self.amount: Decimal | None = None


def _refund_reason(
    provider: PaymentProvider,
    payment: Payment,
    order: Order,
    vendor_order: VendorOrder,
    check: _ChargeCheck,
) -> str:
    """Why this package must not be paid because of a refund or cancellation, or "" when it may.

    Order and package state first (cheap), then the provider's own view of the charge: a refund
    made in the Stripe dashboard is not in our states yet. Any refund, full or partial, blocks;
    a charge that cannot be read blocks too (nothing is paid on uncertainty) and is retried."""
    if order.state in _NO_TRANSFER_ORDER_STATES and not (
        order.state == "partially_refunded" and recorded_refunded(order) > 0
    ):
        return f"order is {order.state}"
    if vendor_order.state in _NO_TRANSFER_PACKAGE_STATES:
        return f"package is {vendor_order.state}"
    reader = getattr(provider, "refunded_amount", None)
    if reader is None:
        return ""
    if check.amount is None:
        try:
            check.amount = reader(
                provider_ref=payment.provider_ref,
                charge_ref=payment.charge_ref,
                currency=payment.currency,
            )
        except ProviderError as error:
            logger.warning("refund state unreadable for %s: %s", payment.provider_ref, error)
            return "refund state of the charge could not be read"
    if check.amount > recorded_refunded(order):
        return "charge refunded at the provider"  # more than the platform recorded
    return ""


def recorded_refunded(order: Order) -> Decimal:
    """What the platform itself refunded on this order's payment (`Refund` rows that moved money).
    A refund at the provider above this figure was made elsewhere, e.g. in the Stripe dashboard."""
    total = Refund.objects.filter(
        vendor_order__order=order,
        channel__in=[RefundChannel.THROUGH_PLATFORM, RefundChannel.PLATFORM_INITIATED],
    ).aggregate(total=Sum("refunded_gross"))["total"]
    return engine.money(total or Decimal("0"))


def _noted_without_account(
    vendor_order: VendorOrder, line: settlement.VendorSettlement, ctx: AuditContext
) -> None:
    """Audit once per vendor order that no transfer is due yet: the ledger balance is the
    receivable (the platform owes the vendor `vendor_net`), settled when the account is ready."""
    _problem_once(
        TRANSFER_WITHOUT_ACCOUNT,
        vendor_order,
        {"receivable": line.vendor_net, "reason": "no ready payout account"},
        ctx,
    )


def _send(
    provider: PaymentProvider,
    payment: Payment,
    order: Order,
    line: settlement.VendorSettlement,
    amount: Decimal,
    ctx: AuditContext,
    outcome: Outcome,
) -> None:
    vendor_order = line.vendor_order
    try:
        result = provider.transfer(
            TransferRequest(
                order_ref=order.transfer_group or order.number,
                provider_ref=payment.provider_ref,
                charge_ref=payment.charge_ref,
                vendor_account_ref=line.account_ref,
                amount=amount,
                currency=payment.currency,
                idempotency_key=f"transfer:{order.public_id}:{vendor_order.vendor.public_id}",
                reference=str(vendor_order.public_id),
            )
        )
    except ProviderError as error:
        outcome.failed += 1
        _problem(TRANSFER_FAILED, vendor_order, {"reason": str(error)}, ctx)
        logger.warning("transfer failed for %s: %s", vendor_order.public_id, error)
        return
    try:
        services.record_entry(
            vendor_order.vendor,
            kind=engine.TRANSFER,
            amount=-amount,
            idempotency_key=key(vendor_order, engine.TRANSFER),
            ctx=ctx,
            vendor_order=vendor_order,
            source_ref=order.number,
            external_ref=result.transfer_ref,
            gateway_account=line.account_ref,
            memo="vendor net transferred to the connected account"
            + (" (reduced by a refund)" if amount != line.vendor_net else ""),
        )
    except _POSTING_ERRORS as error:  # the money moved: the next sweep records it (same transfer)
        outcome.failed += 1
        _problem(TRANSFER_FAILED, vendor_order, {"reason": f"not recorded: {error}"}, ctx)
        return
    outcome.transfers.append(result.transfer_ref)


def pending_payments(*, now: dt.datetime | None = None) -> list[Payment]:
    """Payments of the lookback window (`PAYMENT_TRANSFER_LOOKBACK_DAYS`, 30) whose order is paid
    and that still lack a ledger line or a transfer for some vendor package."""
    moment = now or timezone.now()
    days = int(getattr(settings, "PAYMENT_TRANSFER_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    pending: list[Payment] = []
    recent = Payment.objects.filter(succeeded_at__gte=moment - dt.timedelta(days=days))
    for payment in recent.order_by("id"):
        order = paid_order(payment)
        if order is None:
            continue
        for vendor_order in order.vendor_orders.all():
            transferred = LedgerEntry.objects.filter(
                idempotency_key=key(vendor_order, engine.TRANSFER)
            ).exists()
            if not transferred and not _refunded_away(vendor_order):
                pending.append(payment)
                break
    return pending


def _refunded_away(vendor_order: VendorOrder) -> bool:
    """A package whose refunds took everything the transfer would have sent (PAY.4)."""
    if not moved_refunds(vendor_order).exists():
        return False
    return transferable_net(vendor_order, Decimal("0")) <= 0


def settle_pending(*, now: dt.datetime | None = None) -> Outcome:
    """The periodic sweep: retry every payment that is not fully settled. Idempotent."""
    total = Outcome()
    for payment in pending_payments(now=now):
        done = transfer_payment(payment)
        total.posted += done.posted
        total.transfers.extend(done.transfers)
        total.failed += done.failed
        total.skipped += done.skipped
        total.blocked += done.blocked
        total.refunded += done.refunded
    return total

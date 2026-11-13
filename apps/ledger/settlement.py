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
from .models import LedgerEntry

logger = logging.getLogger(__name__)

SETTLEMENT_ACTOR = "system:order-settlement"
PROBLEM_MODEL = "ledger.settlement"
POSTING_BLOCKED = "ledger.posting_blocked"
SETTLEMENT_BLOCKED_VAT_MODE = "ledger.settlement_blocked_vat_mode"
TRANSFER_FAILED = "ledger.transfer_failed"
TRANSFER_WITHOUT_ACCOUNT = "ledger.transfer_without_account"
TRANSFER_BLOCKED = "ledger.transfer_blocked"
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
    """The paid order this payment settles, or None (unknown order, not paid, other amount)."""
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
        return outcome
    plan = settlement.order_settlement(order)
    paid_total = sum((line.vendor_net for line in plan.vendors if line.transferable), Decimal("0"))
    for line in plan.vendors:
        vendor_order = line.vendor_order
        if LedgerEntry.objects.filter(idempotency_key=key(vendor_order, engine.TRANSFER)).exists():
            continue
        if not LedgerEntry.objects.filter(idempotency_key=key(vendor_order, engine.SALE)).exists():
            continue  # not posted (blocked above): never transfer without ledger lines
        if line.vendor_net <= 0:
            continue
        if not line.account_ref:
            outcome.skipped += 1
            _noted_without_account(vendor_order, line, ctx)
            continue
        if paid_total > payment.amount:
            outcome.blocked += 1
            _problem_once(
                TRANSFER_BLOCKED,
                vendor_order,
                {"reason": "the vendor shares exceed the payment", "payment": payment.amount},
                ctx,
            )
            continue
        _send(provider, payment, order, line, ctx, outcome)
    return outcome


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
                amount=line.vendor_net,
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
            amount=-line.vendor_net,
            idempotency_key=key(vendor_order, engine.TRANSFER),
            ctx=ctx,
            vendor_order=vendor_order,
            source_ref=order.number,
            external_ref=result.transfer_ref,
            gateway_account=line.account_ref,
            memo="vendor net transferred to the connected account",
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
            if not transferred:
                pending.append(payment)
                break
    return pending


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
    return total

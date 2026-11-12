"""Ledger services (VEN.4): record an entry, balance and statement of a vendor.

`record_entry` is the only writer. It is idempotent on the key, takes `dry_run=True` and audits
every entry (`ledger.entry_recorded`). Balances are sums of the entries, never stored per vendor
anywhere else; the running `balance_after` of each row is set by the database.
"""

import datetime as dt
from collections.abc import Iterable, Iterator
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import QuerySet, Sum

from apps.core import audit
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import PlatformOperator
from apps.core.services import current_operator, run_mutation
from apps.orders.models import VendorOrder
from apps.vendors.models import Vendor

from . import engine
from .models import LedgerEntry

ENTRY_MODEL = "ledger.ledgerentry"
ENTRY_RECORDED = "ledger.entry_recorded"


def entry_snapshot(entry: LedgerEntry) -> dict[str, object]:
    """What the audit log keeps of an entry (`vendor` is the public id: it files the audit entry
    under the vendor)."""
    return {
        "vendor": str(entry.vendor.public_id),
        "vendor_order": str(entry.vendor_order.public_id) if entry.vendor_order else None,
        "kind": entry.kind,
        "amount": entry.amount,
        "currency": entry.currency,
        "idempotency_key": entry.idempotency_key,
        "source_ref": entry.source_ref,
        "external_ref": entry.external_ref,
        "gateway_account": entry.gateway_account,
        "memo": entry.memo,
    }


def _same_content(entry: LedgerEntry, candidate: LedgerEntry) -> bool:
    return all(
        getattr(entry, f) == getattr(candidate, f)
        for f in ("vendor_id", "vendor_order_id", "kind", "amount", "currency", "source_ref")
    )


def record_entry(
    vendor: Vendor,
    *,
    kind: str,
    amount: Decimal,
    idempotency_key: str,
    ctx: AuditContext,
    vendor_order: VendorOrder | None = None,
    source_ref: str = "",
    external_ref: str = "",
    gateway_account: str = "",
    memo: str = "",
    currency: str = engine.CURRENCY,
    platform_entity: PlatformOperator | None = None,
    dry_run: bool = False,
) -> ChangeSet:
    """Append one entry for `vendor`; the amount is signed from the vendor's side.

    Repeating a call with the same `idempotency_key` and the same content changes nothing (an
    empty ChangeSet); the same key with other content raises `engine.IdempotencyConflict`.
    `platform_entity` defaults to `current_operator()`. Raises `engine.InvalidEntry` for an
    unknown kind, a wrong sign (a sale is a credit, a commission a debit; `adjustment` is
    either), a zero amount, a currency other than EUR, a blank key or a vendor order of another
    vendor, also on dry-run. A balance may go negative: it is never refused.
    """
    if not idempotency_key.strip():
        raise engine.InvalidEntry("an idempotency key is required")
    engine.check_currency(currency)
    rounded = engine.check_entry(kind, amount)
    if vendor_order is not None and vendor_order.vendor_id != vendor.pk:
        raise engine.InvalidEntry("the vendor order belongs to another vendor")
    entry = LedgerEntry(
        vendor=vendor,
        vendor_order=vendor_order,
        kind=kind,
        amount=rounded,
        currency=currency,
        platform_entity=platform_entity or current_operator(),
        idempotency_key=idempotency_key,
        source_ref=source_ref,
        external_ref=external_ref,
        gateway_account=gateway_account,
        created_by=ctx.actor_label[:128],
        memo=memo,
    )
    existing = LedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return _replay(existing, entry)
    changes = ChangeSet()
    changes.create(ENTRY_MODEL, str(entry.public_id), entry_snapshot(entry))
    try:
        with transaction.atomic():
            return run_mutation(
                changes, entry.save, ctx=ctx, action=ENTRY_RECORDED, dry_run=dry_run
            )
    except IntegrityError:
        existing = LedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing is None:
            raise
        return _replay(existing, entry)  # a concurrent call won the key


def _replay(existing: LedgerEntry, candidate: LedgerEntry) -> ChangeSet:
    if not _same_content(existing, candidate):
        raise engine.IdempotencyConflict(
            f"idempotency key {candidate.idempotency_key!r} is already used by another entry"
        )
    return ChangeSet()


def vendor_balance(vendor: Vendor, until: dt.datetime | None = None) -> Decimal:
    """What the platform owes `vendor`: the sum of its entries up to `until` (inclusive; all of
    them by default). Negative when the vendor owes the platform."""
    entries = LedgerEntry.objects.filter(vendor=vendor)
    if until is not None:
        entries = entries.filter(created_at__lte=until)
    total = entries.aggregate(total=Sum("amount"))["total"]
    return engine.money(total or Decimal("0"))


def vendor_statement(vendor: Vendor, start: dt.datetime, end: dt.datetime) -> engine.Statement:
    """The vendor's entries from `start` (inclusive) to `end` (exclusive): opening balance,
    totals by kind, credits, debits and closing balance."""
    before = LedgerEntry.objects.filter(vendor=vendor, created_at__lt=start)
    opening = before.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    rows = (
        LedgerEntry.objects.filter(vendor=vendor, created_at__gte=start, created_at__lt=end)
        .values("kind")
        .annotate(total=Sum("amount"))
    )
    return engine.build_statement(opening, {row["kind"]: row["total"] for row in rows})


# --- Vendor-facing reads (VEN.9)

EXPORT_CSV = "ledger.export_csv"
VENDOR_MODEL = "vendors.vendor"
CSV_HEADER = (
    "date",
    "kind",
    "amount",
    "currency",
    "balance_after",
    "order",
    "reference",
    "memo",
)


def filter_entries(
    entries: QuerySet[LedgerEntry],
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    kinds: Iterable[str] | None = None,
) -> QuerySet[LedgerEntry]:
    """`entries` narrowed to a period (`start` inclusive, `end` exclusive) and/or kinds, newest
    first. Callers pass an already vendor-scoped queryset."""
    if start is not None:
        entries = entries.filter(created_at__gte=start)
    if end is not None:
        entries = entries.filter(created_at__lt=end)
    if kinds is not None:
        entries = entries.filter(kind__in=list(kinds))
    return entries.select_related("vendor_order__order").order_by("-id")


def csv_rows(entries: Iterable[LedgerEntry]) -> Iterator[list[str]]:
    """The export: header, then one row per entry. Text cells are formula-safe; amounts are plain
    signed numbers."""
    yield list(CSV_HEADER)
    for entry in entries:
        order = entry.vendor_order.order.number if entry.vendor_order else ""
        yield [
            entry.created_at.isoformat(timespec="seconds"),
            entry.kind,
            engine.signed(entry.amount),
            entry.currency,
            f"{entry.balance_after:.2f}",
            engine.csv_safe(order),
            engine.csv_safe(entry.external_ref),
            engine.csv_safe(entry.memo),
        ]


def record_export(
    vendor: Vendor,
    *,
    ctx: AuditContext,
    rows: int,
    start: dt.date | None,
    end: dt.date | None,
    kinds: Iterable[str] | None,
) -> None:
    """Audit one CSV export of the vendor's ledger (counts and filters only, never the rows)."""
    audit.record(
        ctx,
        EXPORT_CSV,
        VENDOR_MODEL,
        str(vendor.public_id),
        None,
        {
            "rows": rows,
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "kinds": sorted(kinds) if kinds else None,
        },
    )

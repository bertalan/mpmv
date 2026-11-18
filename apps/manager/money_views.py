"""The vendor money pages of the shop space (VEN.9, CORE.17): balance, transactions, payouts,
period statement and CSV ledger extract of the selected vendor's own ledger.

Thin, read-only adapters over `apps.ledger`: the rows come from `scoped(LedgerEntry)` (CORE.3
`for_actor`, narrowed to the selected vendor), the periods and CSV rules from `ledger.engine`,
the statement and export from `ledger.services`. Nothing here writes to the ledger; the export
only writes its audit entry. The section is declared for the `payouts` action of the role matrix,
so the vendor owner sees it and the shop's staff get a 404.

Vendor-facing fields only: the provider reference (`external_ref`) is shown, the entry's `memo`
and the other platform working fields are not (assumed, to confirm with the owner).
"""

import csv
import datetime as dt
from dataclasses import replace
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.ledger import engine
from apps.ledger.models import LedgerEntry
from apps.ledger.services import (
    csv_rows,
    filter_entries,
    record_export,
    vendor_balance,
    vendor_statement,
)
from apps.vendors.models import Vendor

from .views import ManagerView

SECTION = "shop.money"
PAGE_SIZE = 25
RECENT = 5

KIND_LABELS: dict[str, StrOrPromise] = {
    engine.SALE: _("Sale"),
    engine.SHIPPING: _("Shipping"),
    engine.COMMISSION: _("Commission"),
    engine.PAYMENT_FEE: _("Payment fee"),
    engine.REFUND: _("Refund"),
    engine.COMMISSION_REVERSAL: _("Commission given back"),
    engine.PAYMENT_FEE_REVERSAL: _("Payment fee given back"),
    engine.TRANSFER_REVERSAL: _("Transfer taken back"),
    engine.REFUND_FEE: _("Refund fee"),
    engine.ADJUSTMENT: _("Adjustment"),
    engine.PAYOUT: _("Payout"),
    engine.TRANSFER: _("Transfer"),
    engine.PLAN_FEE: _("Plan fee"),
    engine.BUNDLE_DISCOUNT_VENDOR_FUNDED: _("Bundle discount funded by you"),
    engine.BUNDLE_DISCOUNT_PLATFORM_FUNDED: _("Bundle discount funded by the platform"),
}
PERIOD_ERRORS: dict[str, StrOrPromise] = {
    engine.PERIOD_BAD_DATE: _("Use dates like 2026-03-31. The period filter was ignored."),
    engine.PERIOD_REVERSED: _(
        "The start date is after the end date. The period filter was ignored."
    ),
    engine.PERIOD_INCOMPLETE: _(
        "A statement needs a start and an end date. The current month is shown."
    ),
}


def kind_label(kind: str) -> str:
    return str(KIND_LABELS.get(kind, kind))


def business_tz() -> ZoneInfo:
    """The zone of the vendor's calendar days: periods, "this month" and the dates shown and
    exported are cut there, not in the server's UTC (like `termination_views._effective_at`)."""
    return ZoneInfo(settings.BUSINESS_TIME_ZONE)


class Filters:
    """The period and kind read from the query string (or the export form)."""

    def __init__(self, data: Any, *, default_month: bool = False, closed: bool = False) -> None:
        self.period = engine.read_period(
            str(data.get("from", "")),
            str(data.get("to", "")),
            today=timezone.localdate(timezone=business_tz()),
            default_month=default_month,
            closed=closed,
        )
        kind = str(data.get("kind", ""))
        self.kind = kind if kind in KIND_LABELS else ""

    @property
    def first(self) -> dt.date | None:
        return self.period.first

    @property
    def last(self) -> dt.date | None:
        return self.period.last

    @property
    def error(self) -> str:
        return str(PERIOD_ERRORS.get(self.period.error, ""))

    @property
    def bounds(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        return engine.period_bounds(self.first, self.last, business_tz())

    @property
    def kinds(self) -> list[str] | None:
        return [self.kind] if self.kind else None

    def query(self) -> dict[str, str]:
        found = {
            "from": self.first.isoformat() if self.first else "",
            "to": self.last.isoformat() if self.last else "",
            "kind": self.kind,
        }
        return {k: v for k, v in found.items() if v}


def entry_row(entry: LedgerEntry) -> dict[str, Any]:
    """What the vendor sees of one entry (never its memo or other platform working fields)."""
    sub = entry.vendor_order
    return {
        "when": timezone.localtime(entry.created_at, business_tz()),
        "kind": kind_label(entry.kind),
        "amount": entry.amount,
        "balance_after": entry.balance_after,
        "order": sub.order.number if sub else "",
        "order_url": reverse("manager:shop:order", args=[sub.public_id]) if sub else "",
        "reference": entry.external_ref,
    }


class MoneyView(ManagerView):
    """Base of the money pages: the selected vendor and its own entries."""

    section = SECTION

    def vendor(self) -> Vendor:
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        return vendor

    def entries(self) -> QuerySet[LedgerEntry]:
        return self.scoped(LedgerEntry)

    def page_context(
        self, rows: QuerySet[LedgerEntry], filters: Filters, url_name: str
    ) -> dict[str, Any]:
        page = Paginator(rows, PAGE_SIZE).get_page(self.request.GET.get("page"))

        def link(number: int) -> str:
            return "?" + urlencode({**filters.query(), "page": str(number)})

        return {
            "page_obj": page,
            "rows": [entry_row(e) for e in page.object_list],
            "previous_url": link(page.previous_page_number()) if page.has_previous() else "",
            "next_url": link(page.next_page_number()) if page.has_next() else "",
            "filters": filters,
            "filter_error": filters.error,
            "kinds": [(k, str(label)) for k, label in KIND_LABELS.items()],
            "form_action": reverse(url_name),
        }


class MoneyOverviewView(MoneyView):
    """The current balance, the latest payouts and the latest movements."""

    template_name = "manager/shop/money.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.vendor()
        rows = self.entries()
        context.update(
            balance=vendor_balance(vendor),
            currency=engine.CURRENCY,
            recent=[entry_row(e) for e in filter_entries(rows)[:RECENT]],
            recent_payouts=[
                entry_row(e) for e in filter_entries(rows, kinds=engine.PAYOUT_KINDS)[:RECENT]
            ],
        )
        return context


class TransactionListView(MoneyView):
    """Every movement of the vendor's balance, newest first, with the running balance."""

    template_name = "manager/shop/money_transactions.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        self.vendor()
        filters = Filters(self.request.GET)
        start, end = filters.bounds
        rows = filter_entries(self.entries(), start=start, end=end, kinds=filters.kinds)
        context.update(self.page_context(rows, filters, "manager:shop:money_transactions"))
        context["export_url"] = reverse("manager:shop:money_export")
        return context


class PayoutListView(MoneyView):
    """What was sent to the vendor: entries of kind payout and transfer."""

    template_name = "manager/shop/money_payouts.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        self.vendor()
        filters = Filters(self.request.GET)
        filters.kind = ""
        start, end = filters.bounds
        rows = filter_entries(self.entries(), start=start, end=end, kinds=engine.PAYOUT_KINDS)
        context.update(self.page_context(rows, filters, "manager:shop:money_payouts"))
        return context


class StatementView(MoneyView):
    """A period statement from `vendor_statement`: opening balance, totals by kind, closing
    balance (the current month by default). A non-fiscal document with a stable reference."""

    template_name = "manager/shop/money_statement.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.vendor()
        filters = Filters(self.request.GET, default_month=True, closed=True)
        start, end = filters.bounds
        if start is None or end is None:  # never: a closed period always has both ends
            raise Http404("No period.")
        statement = vendor_statement(vendor, start, end)
        context.update(
            filters=filters,
            filter_error=filters.error,
            statement=statement,
            by_kind=[
                {"kind": kind_label(k), "total": total}
                for k, total in sorted(statement.by_kind.items())
            ],
            currency=engine.CURRENCY,
            non_fiscal=engine.NON_FISCAL_LABEL,
            reference=engine.extract_reference(str(vendor.public_id), filters.first, filters.last),
            form_action=reverse("manager:shop:money_statement"),
        )
        return context


class ExportView(MoneyView):
    """POST (CSRF, audited): the transactions of a period as a CSV ledger extract. The period is
    always bounded: the current month when none is given."""

    http_method_names = ["post", "options"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.vendor()
        filters = Filters(request.POST, default_month=True, closed=True)
        start, end = filters.bounds
        reference = engine.extract_reference(str(vendor.public_id), filters.first, filters.last)
        entries = filter_entries(self.entries(), start=start, end=end, kinds=filters.kinds)
        # A list for the audited row count; the period is always closed (a month by default).
        lines = list(csv_rows(entries.iterator(), reference=reference, tz=business_tz()))
        record_export(
            vendor,
            ctx=replace(audit_context(request), source=AuditLog.Source.VENDOR),
            rows=len(lines) - 2,
            reference=reference,
            first=filters.first,
            last=filters.last,
            kinds=filters.kinds,
        )
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{reference}.csv"'
        response["Cache-Control"] = "private, no-store"
        csv.writer(response, lineterminator="\r\n").writerows(lines)
        return response

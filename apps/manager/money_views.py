"""The vendor money pages of the shop space (VEN.9, CORE.17): balance, transactions, payouts,
period statement and CSV export of the selected vendor's own ledger.

Thin adapters over `apps.ledger`: the rows come from `scoped(LedgerEntry)` (CORE.3 `for_actor`,
narrowed to the selected vendor), the arithmetic from `ledger.services` and `ledger.engine`. The
section is declared for the `payouts` action, so the vendor owner sees it and staff get a 404.
"""

import csv
import datetime as dt
from dataclasses import replace
from typing import Any

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.ledger import engine
from apps.ledger.models import LedgerEntry
from apps.ledger.services import csv_rows, filter_entries, record_export, vendor_statement

from .views import ManagerView

SECTION = "shop.money"
PAGE_SIZE = 25
RECENT = 5

KIND_LABELS = {
    engine.SALE: _("Sale"),
    engine.SHIPPING: _("Shipping"),
    engine.COMMISSION: _("Commission"),
    engine.PAYMENT_FEE: _("Payment fee"),
    engine.REFUND: _("Refund"),
    engine.COMMISSION_REVERSAL: _("Commission reversal"),
    engine.REFUND_FEE: _("Refund fee"),
    engine.ADJUSTMENT: _("Adjustment"),
    engine.PAYOUT: _("Payout"),
    engine.TRANSFER: _("Transfer"),
    engine.PLAN_FEE: _("Plan fee"),
    engine.BUNDLE_DISCOUNT_VENDOR_FUNDED: _("Bundle discount funded by you"),
    engine.BUNDLE_DISCOUNT_PLATFORM_FUNDED: _("Bundle discount funded by the platform"),
}


def kind_label(kind: str) -> str:
    return str(KIND_LABELS.get(kind, kind))


def statement_extras(vendor: Any, start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    """Hook for the statement lines of CMP.2 (draft lane-b/cmp-2-statement-lines).

    Returns extra sections for the period statement page, each `{"title": str, "rows": [...]}`.
    Empty until CMP.2 lands: the page then shows only the ledger totals of `vendor_statement`.
    """
    return []


class Filters:
    """The period and kind filters read from the query string (or the export form)."""

    def __init__(self, data: Any, *, default_month: bool = False) -> None:
        self.error = ""
        first = engine.parse_date(data.get("from", ""))
        last = engine.parse_date(data.get("to", ""))
        if (data.get("from") and first is None) or (data.get("to") and last is None):
            self.error = str(_("Use dates like 2026-03-31. The filter was ignored."))
            first = last = None
        elif first and last and first > last:
            self.error = str(_("The start date is after the end date. The filter was ignored."))
            first = last = None
        if default_month and first is None and last is None:
            first, last = engine.month_bounds(timezone.localdate())
        self.first, self.last = first, last
        kind = data.get("kind", "")
        self.kind = kind if kind in KIND_LABELS else ""

    @property
    def bounds(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        return engine.period_bounds(self.first, self.last, timezone.get_current_timezone())

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
    return {
        "when": entry.created_at,
        "kind": kind_label(entry.kind),
        "amount": entry.amount,
        "balance_after": entry.balance_after,
        "order": entry.vendor_order.order.number if entry.vendor_order else "",
        "reference": entry.external_ref,
        "memo": entry.memo,
    }


class MoneyView(ManagerView):
    section = SECTION

    def entries(self) -> Any:
        return self.scoped(LedgerEntry)

    def page_context(self, rows: Any, filters: Filters, url_name: str) -> dict[str, Any]:
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
        rows = self.entries()
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        latest = filter_entries(rows)
        context.update(
            balance=engine.balance(rows.values_list("amount", flat=True)),
            currency=engine.CURRENCY,
            recent=[entry_row(e) for e in latest[:RECENT]],
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
        filters = Filters(self.request.GET)
        filters.kind = ""
        start, end = filters.bounds
        rows = filter_entries(self.entries(), start=start, end=end, kinds=engine.PAYOUT_KINDS)
        context.update(self.page_context(rows, filters, "manager:shop:money_payouts"))
        return context


class StatementView(MoneyView):
    """A period statement: opening balance, totals by kind, closing balance (month by default)."""

    template_name = "manager/shop/money_statement.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        filters = Filters(self.request.GET, default_month=True)
        if filters.first is None or filters.last is None:  # one side only: use a whole month
            error = filters.error
            filters = Filters({}, default_month=True)
            filters.error = error
        start, end = filters.bounds
        vendor = self.manager.vendor
        if vendor is None or start is None or end is None:
            raise Http404("No vendor selected.")
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
            extras=statement_extras(vendor, start, end),
            form_action=reverse("manager:shop:money_statement"),
        )
        return context


class ExportView(MoneyView):
    """POST: the transactions of a period as CSV, audited. A period is required (default: the
    current month) so an export is always bounded."""

    http_method_names = ["post", "options"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        filters = Filters(request.POST, default_month=True)
        start, end = filters.bounds
        entries = filter_entries(self.entries(), start=start, end=end, kinds=filters.kinds)
        lines = list(csv_rows(entries.iterator()))
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        record_export(
            vendor,
            ctx=ctx,
            rows=len(lines) - 1,
            start=filters.first,
            end=filters.last,
            kinds=filters.kinds,
        )
        first = filters.first.isoformat() if filters.first else "start"
        last = filters.last.isoformat() if filters.last else "end"
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="transactions-{first}-{last}.csv"'
        response["Cache-Control"] = "private, no-store"
        csv.writer(response).writerows(lines)
        return response

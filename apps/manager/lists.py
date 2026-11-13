"""The Manager's base list view: filters, pagination and bulk actions with dry-run (CORE.17).

A section's list declares its rows (`get_queryset`), its columns, its filters and its bulk
actions. The base class passes `get_queryset()` through `scoped()` for the listing and for the
bulk selection alike, so a list that forgets it still shows and accepts only the selected
vendor's rows (tenant isolation, CORE.3). Every bulk action declares who may run it (`allowed`,
no default):

    class OrderList(ManagerListView):
        section = "shop.orders"
        columns = (Column(_("Order"), lambda o: o.number), Column(_("Total"), money, numeric=True))
        filters = (ListFilter("status", _("Status"), OrderStatus.choices, lookup="status"),)
        bulk_actions = (
            BulkAction("ship", _("Mark shipped"), ship_orders, step_up=True,
                       allowed=vendor_action("orders")),
        )

        def get_queryset(self):
            return self.scoped(Order).order_by("-placed_at")

A bulk action calls a service `run(objects, *, ctx, dry_run) -> ChangeSet` (spec 01: every
mutating service has a dry-run). The POST of a selection first renders the dry-run `ChangeSet`;
only the confirmation (`confirm`) runs it for real, behind an MFA step-up when the action is
sensitive (spec 02 Authentication). A selected id outside the list's rows (another vendor's
object, a missing or malformed one) answers 404 and runs nothing.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Page, Paginator
from django.db.models import Model, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django_stubs_ext import StrOrPromise

from apps.accounts.decorators import mfa_redirect, requires_level_method
from apps.accounts.mfa_session import is_recently_verified
from apps.accounts.services import has_second_factor
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet

from .navigation import Check
from .views import ManagerView

type BulkRun = Callable[..., ChangeSet]


@dataclass(frozen=True)
class Column:
    label: StrOrPromise
    value: Callable[[Any], Any]
    numeric: bool = False


@dataclass(frozen=True)
class ListFilter:
    """A choice filter: `?<name>=<value>` narrows the rows by `lookup`; other values are ignored."""

    name: str
    label: StrOrPromise
    choices: Sequence[tuple[str, str]]
    lookup: str

    def apply[M: Model](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        if value in {key for key, _label in self.choices}:
            return rows.filter(**{self.lookup: value})
        return rows


@dataclass(frozen=True)
class BulkAction:
    name: str
    label: StrOrPromise
    run: BulkRun
    step_up: bool = False  # sensitive: the confirmation needs a recent second factor
    allowed: Check = field(kw_only=True)  # no default: deny unless a check is declared


class ManagerListView(ManagerView):
    template_name = "manager/list.html"
    preview_template_name = "manager/bulk_preview.html"
    paginate_by: ClassVar[int] = 25
    columns: ClassVar[Sequence[Column]] = ()
    filters: ClassVar[Sequence[ListFilter]] = ()
    bulk_actions: ClassVar[Sequence[BulkAction]] = ()

    def get_queryset(self) -> QuerySet[Any]:
        raise NotImplementedError

    def rows(self) -> QuerySet[Any]:
        """`get_queryset()` narrowed by `scoped()` again, whatever the subclass returned."""
        return self.scoped(self.get_queryset())

    # --- Listing

    def filtered(self) -> QuerySet[Any]:
        rows = self.rows()
        for list_filter in self.filters:
            rows = list_filter.apply(rows, self.request.GET.get(list_filter.name, ""))
        return rows

    def active_query(self) -> str:
        """The filter part of the query string, kept by pagination and after a bulk action."""
        pairs = [
            (f.name, self.request.GET[f.name])
            for f in self.filters
            if self.request.GET.get(f.name, "") in {key for key, _label in f.choices}
        ]
        return urlencode(pairs)

    def available_actions(self) -> list[BulkAction]:
        return [action for action in self.bulk_actions if action.allowed(self.manager)]

    def _page_url(self, number: int) -> str:
        query = self.active_query()
        return f"?{query + '&' if query else ''}page={number}"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        page: Page[Any] = Paginator(self.filtered(), self.paginate_by).get_page(
            self.request.GET.get("page")
        )
        context.update(
            columns=self.columns,
            rows=[
                {"id": str(obj.public_id), "cells": [c.value(obj) for c in self.columns]}
                for obj in page.object_list
            ],
            page_obj=page,
            previous_url=self._page_url(page.previous_page_number()) if page.has_previous() else "",
            next_url=self._page_url(page.next_page_number()) if page.has_next() else "",
            filters=[
                {
                    "name": f.name,
                    "label": f.label,
                    "choices": f.choices,
                    "value": self.request.GET.get(f.name, ""),
                }
                for f in self.filters
            ],
            bulk_actions=self.available_actions(),
        )
        return context

    # --- Bulk actions

    @requires_level_method("bulk_edit")  # group 2: every list view inherits the gate
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        action = next(
            (a for a in self.available_actions() if a.name == request.POST.get("action")), None
        )
        if action is None:
            raise Http404("No such action.")
        ids = request.POST.getlist("selected")
        if not ids:
            context = self.get_context_data(**kwargs)
            context["selection_error"] = _("Select at least one row.")
            return self.render_to_response(context)
        objects = self.selected(ids)
        confirmed = request.POST.get("confirm") == "1"
        if confirmed and action.step_up and not is_recently_verified(request):
            user = request.user
            enrolled = user.is_authenticated and has_second_factor(user)
            target = "mfa:verify" if enrolled else "mfa:setup"
            return mfa_redirect(request, target, next_url=self.list_url())
        changes = action.run(objects, ctx=audit_context(request), dry_run=not confirmed)
        if not confirmed:
            return self.preview(action, ids, changes)
        messages.success(
            request,
            ngettext(
                "%(action)s: %(count)d row done.", "%(action)s: %(count)d rows done.", len(objects)
            )
            % {"action": action.label, "count": len(objects)},
        )
        return HttpResponseRedirect(self.list_url())

    def selected(self, ids: Sequence[str]) -> list[Any]:
        """The selected rows among the list's own, or 404 when any id is not one of them."""
        try:
            wanted = {uuid.UUID(value) for value in ids}
        except ValueError:
            raise Http404("No such row.") from None
        objects = list(self.rows().filter(public_id__in=wanted))
        if len(objects) != len(wanted):
            raise Http404("No such row.")
        return objects

    def list_url(self) -> str:
        query = self.active_query()
        return self.request.path + (f"?{query}" if query else "")

    def preview(self, action: BulkAction, ids: Sequence[str], changes: ChangeSet) -> HttpResponse:
        context = self.get_context_data()
        context.update(
            action=action, selected=list(ids), changes=changes.changes, list_url=self.list_url()
        )
        return self.response_class(
            request=self.request,
            template=[self.preview_template_name],
            context=context,
            using=self.template_engine,
        )

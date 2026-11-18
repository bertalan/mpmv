"""Carts prepared for a customer from a past order (ORD.32): thin adapters over
`apps.orders.staff_drafts`, in the platform space for Support and the platform owner.

From a platform order page, staff copy the whole order into a draft for the same customer, at
today's prices and stock, may lower or raise quantities up to the stock or remove lines (never a
price: the draft has none) and email the customer a one-time link, only to the order's account
or guest email, at most 3 times a day. The customer opens the link, checks the cart and pays
through the normal checkout; staff never place or pay for the customer. Every step is a POST
with CSRF and a mandatory note, audited with the staff role. A Catalog, Finance or Configuration
member gets a 404 (no row in the CORE.18 matrix), as anyone outside the platform space.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, ClassVar, Final

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.cart import engine as cart_engine
from apps.core.audit import audit_context
from apps.orders import engine as orders_engine
from apps.orders import staff_drafts
from apps.orders.models import Order, StaffDraft, StaffDraftLine, StaffDraftState

from .views import ManagerView

SECTION: Final = "platform.order_drafts"  # a row of roles.MATRIX: Support, platform owner

SKIPPED_LABELS = {
    orders_engine.REORDER_RETIRED: gettext_lazy("not sold any more"),
    orders_engine.REORDER_OUT_OF_STOCK: gettext_lazy("out of stock"),
    orders_engine.REORDER_NOT_DELIVERABLE: gettext_lazy(
        "the shop cannot deliver it to the order's country"
    ),
}

STATE_LABELS: dict[StaffDraftState, Any] = {
    StaffDraftState.OPEN: gettext_lazy("Open: you may change it and send the link"),
    StaffDraftState.OPENED: gettext_lazy("Opened by the customer: the items are in their cart"),
}


class DraftNoteForm(forms.Form):
    note = forms.CharField(
        label=gettext_lazy("Note"),
        max_length=staff_drafts.NOTE_MAX,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=gettext_lazy(
            "Required. Why you do this, for example who asked and how. The customer does not "
            "see it."
        ),
    )


def _actor(view: ManagerView) -> User:
    actor = view.manager.user
    if not isinstance(actor, User):
        raise Http404("No Manager page here.")
    return actor


def _money(value: Decimal | None) -> str:
    return f"{value:.2f} EUR" if value is not None else ""


def _draft(public_id: uuid.UUID) -> StaffDraft:
    return get_object_or_404(
        StaffDraft.objects.select_related("source_order", "customer"), public_id=public_id
    )


def order_page_context(order: Order) -> dict[str, Any]:
    """What a platform order page shows to prepare a cart from the order: the open draft's page,
    or the form that makes one; nothing when the order cannot be copied."""
    if not staff_drafts.copyable(order):
        return {}
    existing = staff_drafts.open_draft_of(order)
    if existing is not None:
        return {"staff_draft_url": _draft_url(existing)}
    return {
        "staff_draft_new_url": reverse("manager:platform:order_draft_new", args=[order.public_id]),
        "staff_draft_form": DraftNoteForm(),
    }


def _draft_url(draft: StaffDraft) -> str:
    return reverse("manager:platform:order_draft", args=[draft.public_id])


def _unit_price(line: StaffDraftLine) -> Decimal | None:
    """Today's catalog price of the line's item, as the cart will show it (net, display)."""
    return cart_engine.display_unit_price(
        line.product.price_net,
        "buy",
        line.variant.price_net_override if line.variant is not None else None,
    )


class DraftListView(ManagerView):
    """The open drafts, newest first: the source order, the customer kind, the lines and the
    link. No contact data of the customer."""

    section = SECTION
    template_name = "manager/platform/order_drafts.html"
    paginate_by: ClassVar[int] = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rows = StaffDraft.objects.filter(state=StaffDraftState.OPEN).select_related("source_order")
        page = Paginator(rows.order_by("-created_at", "id"), self.paginate_by).get_page(
            self.request.GET.get("page")
        )
        context.update(
            title=_("Prepared carts"),
            drafts=[
                {
                    "number": draft.source_order.number,
                    "customer": _("Account") if draft.customer_id else _("Guest"),
                    "created": draft.created_at,
                    "lines": draft.lines.count(),
                    "link": _("Sent, usable")
                    if staff_drafts.link_active(draft)
                    else _("Not sent, or no longer usable"),
                    "url": _draft_url(draft),
                }
                for draft in page.object_list
            ],
            page_obj=page,
        )
        return context


class DraftCreateView(ManagerView):
    """POST from a platform order page: copy the whole order into a draft for its customer."""

    section = SECTION
    http_method_names = ["post"]

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        order = get_object_or_404(Order, public_id=kwargs["order"])
        back = request.POST.get("back", "")
        form = DraftNoteForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Write a note: why you prepare this cart, at whose request."))
            return HttpResponseRedirect(back if back.startswith("/manager/") else reverse(
                "manager:platform:order_drafts"
            ))  # fmt: skip
        try:
            result = staff_drafts.draft_from_order(
                order,
                actor=_actor(self),
                note=form.cleaned_data["note"],
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No Manager page here.") from None
        except staff_drafts.DraftRefused as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(reverse("manager:platform:order_drafts"))
        if result.existing:
            messages.info(request, _("This order already has an open prepared cart: here it is."))
        else:
            messages.success(request, _("The cart is prepared. Check it, then send the link."))
        if result.draft is None:  # pragma: no cover - a real run always returns the draft
            raise Http404("No Manager page here.")
        return HttpResponseRedirect(_draft_url(result.draft))


class DraftView(ManagerView):
    """One draft: its lines at today's prices with the stock, the items left out and why, the
    quantity form and the send form."""

    section = SECTION
    template_name = "manager/platform/order_draft.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        draft = _draft(self.kwargs["public_id"])
        lines = staff_drafts.draft_lines(draft)
        now = timezone.now()
        is_open = draft.state == StaffDraftState.OPEN
        rows = []
        for line in lines:
            limit = staff_drafts.line_limit(line)
            price = _unit_price(line)
            rows.append(
                {
                    "key": str(line.public_id),
                    "title": line.title,
                    "variant": line.variant_label,
                    "quantity": line.quantity,
                    "limit": limit,
                    "unit": _money(price),
                    "total": _money(price * line.quantity if price is not None else None),
                }
            )
        context.update(
            title=_("Prepared cart"),
            draft=draft,
            state_label=STATE_LABELS.get(StaffDraftState(draft.state), draft.state),
            is_open=is_open,
            number=draft.source_order.number,
            customer_kind=_("The order's account") if draft.customer_id else _("The guest's email"),
            rows=rows,
            skipped=[
                {**item, "reason": SKIPPED_LABELS.get(item.get("reason", ""), "")}
                for item in draft.skipped
            ],
            link_active=staff_drafts.link_active(draft, now=now),
            link_expires_at=draft.link_expires_at,
            sends_left=staff_drafts.sends_left(draft, now=now),
            max_sends=orders_engine.DRAFT_MAX_SENDS_PER_DAY,
            lines_url=reverse("manager:platform:order_draft_lines", args=[draft.public_id]),
            send_url=reverse("manager:platform:order_draft_send", args=[draft.public_id]),
            note_form=kwargs.get("note_form") or DraftNoteForm(prefix="lines"),
            send_form=kwargs.get("send_form") or DraftNoteForm(prefix="send"),
            action_error=kwargs.get("action_error", ""),
        )
        return context


class _DraftAction(DraftView):
    """A POST on the draft page; a refusal shows the page again with the reason."""

    http_method_names = ["post"]

    def refused(self, message: str, **extra: Any) -> HttpResponse:
        context = self.get_context_data(action_error=message, **extra)
        return self.render_to_response(context, status=409)


class DraftLinesView(_DraftAction):
    """POST: change the quantities (0 removes a line), each up to the stock. A posted price or
    any other field is ignored: quantities are the only thing staff change."""

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        draft = _draft(kwargs["public_id"])
        form = DraftNoteForm(request.POST, prefix="lines")
        if not form.is_valid():
            return self.refused(_("Write a note for the change."), note_form=form)
        quantities: dict[uuid.UUID, int] = {}
        for name in request.POST:
            if not name.startswith("qty-"):
                continue
            try:
                quantities[uuid.UUID(name.removeprefix("qty-"))] = int(request.POST.get(name, ""))
            except ValueError:
                return self.refused(_("A quantity must be a whole number."))
        try:
            changes = staff_drafts.set_quantities(
                draft,
                quantities,
                actor=_actor(self),
                note=form.cleaned_data["note"],
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No Manager page here.") from None
        except staff_drafts.DraftRefused as error:
            return self.refused(str(error))
        if changes.is_empty:
            messages.info(request, _("Nothing changed."))
        else:
            messages.success(
                request,
                _("The cart is changed. A link sent before no longer works: send a new one."),
            )
        return HttpResponseRedirect(_draft_url(draft))


class DraftSendView(_DraftAction):
    """POST: email a new one-time link to the order's account or guest email. Any address in
    the request is ignored: the service reads it from the order."""

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        draft = _draft(kwargs["public_id"])
        form = DraftNoteForm(request.POST, prefix="send")
        if not form.is_valid():
            return self.refused(_("Write a note for the send."), send_form=form)
        try:
            result = staff_drafts.send_link(
                draft,
                actor=_actor(self),
                note=form.cleaned_data["note"],
                ctx=audit_context(request),
            )
        except PermissionDenied:
            raise Http404("No Manager page here.") from None
        except staff_drafts.DraftRefused as error:
            return self.refused(str(error))
        expires = result.expires_at or timezone.now() + dt.timedelta(days=1)
        messages.success(
            request,
            _("The link is sent to the customer. It works once, until %(date)s.")
            % {"date": timezone.localtime(expires).strftime("%Y-%m-%d %H:%M")},
        )
        return HttpResponseRedirect(_draft_url(draft))

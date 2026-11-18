"""Order amendments in the Manager (ORD.20 part 3): thin adapters over `apps.orders.amendments`.

Shop space (the owner and members with ORDERS, own shop only): draft a change of a paid package
in `pending` or `accepted`, see the recomputed preview, revise, discard and apply it. Platform
space (the platform owner and Support): the staff tools of spec 07 (a carrier at the same or a
lower price, an address correction on the customer's request, cancelling the package with a full
refund), each with a mandatory note; and the three amendment settings, read-only.

A critical change (an address change, any refund, a cancellation) asks a fresh passkey: the
service refuses with `PasskeyRequired`, the page answers with the passkey prompt (never cached)
that mints the single-use grant bound to the amendment's digest, then replays the POST. Every
mutation is a POST with a CSRF token; another shop's package is a 404 (CORE.3 scoping).
"""

import datetime as dt
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.cache import add_never_cache_headers
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts import levels
from apps.accounts.decorators import requires_level_method
from apps.accounts.models import User
from apps.core.audit import AuditContext, audit_context
from apps.core.models import AuditLog
from apps.core.session_policy import GROUP_CRITICAL
from apps.orders import amendment_engine as am
from apps.orders import amendments, versions
from apps.orders.models import (
    OPEN_AMENDMENT_STATES,
    AmendmentState,
    OrderAmendment,
    VendorOrder,
    VendorOrderState,
)
from apps.orders.services import PAID_OR_LATER, OrdersFrozen

from .amendment_forms import (
    AmendmentForm,
    CancelPackageForm,
    ShopCancelForm,
    initial_from,
    lines_of,
)
from .spaces import Space
from .views import ManagerView, address_lines

PLATFORM_SECTION: Final = "platform.support_actions"  # a row of roles.MATRIX: Support, owner
SETTINGS_SECTION: Final = "platform.config"  # a row of roles.MATRIX: configuration, owner

KIND_LABEL = {
    am.MINOR: gettext_lazy("Minor change: it applies at once"),
    am.SUBSTANTIAL: gettext_lazy(
        "Substantial change: it needs the customer's acceptance, which is not available yet"
    ),
}
INITIATOR_LABEL = {
    am.VENDOR_OWNER: gettext_lazy("the shop's owner"),
    am.VENDOR_MEMBER: gettext_lazy("a shop member"),
    am.PLATFORM_OWNER: gettext_lazy("the platform owner"),
    am.SUPPORT: gettext_lazy("platform support"),
}
ACTION_LABEL = {
    am.ACTION_ADDRESS: gettext_lazy("a change of the delivery address"),
    am.ACTION_VENDOR_REFUND: gettext_lazy("a refund to the customer"),
    am.ACTION_STAFF_REFUND: gettext_lazy("a refund to the customer by platform staff"),
}


def _money(value: Any) -> str:
    return f"{Decimal(str(value or '0')):.2f} EUR"


def _actor(view: ManagerView) -> User:
    actor = view.manager.user
    if not isinstance(actor, User):
        raise Http404("No Manager page here.")
    return actor


def version_lines(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines of a version as the pages show them: title, quantity, unit and gross."""
    found = []
    for line in am.VersionSnapshot.from_json(snapshot).lines:
        found.append(
            {
                "title": line.title,
                "variant": line.variant_label,
                "sku": line.sku,
                "quantity": line.quantity,
                "unit": _money(line.unit_price_net),
                "gross": _money(line.gross),
            }
        )
    return found


def preview_context(amendment: OrderAmendment) -> dict[str, Any]:
    """The recomputed preview of a draft in plain words: per line before and after, the totals,
    the money moved and the classification with its reasons."""
    preview = amendment.preview
    before = am.VersionSnapshot.from_json(amendment.from_version.snapshot)
    after_raw = preview.get("after_snapshot")
    after = am.VersionSnapshot.from_json(after_raw) if after_raw else None
    lines = []
    for row in preview.get("lines", []):
        old, new = row.get("before"), row.get("after")
        source = before.line(row["line_key"]) or (after.line(row["line_key"]) if after else None)
        lines.append(
            {
                "title": source.title if source else row["line_key"],
                "before": (
                    _("%(qty)s x %(unit)s")
                    % {"qty": old["quantity"], "unit": _money(old["unit_price_net"])}
                    if old
                    else _("not in the order")
                ),
                "after": (
                    _("%(qty)s x %(unit)s")
                    % {"qty": new["quantity"], "unit": _money(new["unit_price_net"])}
                    if new
                    else _("removed")
                ),
            }
        )
    shipping = None
    if after is not None and after.shipping.key != before.shipping.key:
        shipping = {
            "before": before.shipping.label or before.shipping.key,
            "after": after.shipping.label or after.shipping.key,
        }
    refund = Decimal(str(preview.get("refund") or "0"))
    to_pay = Decimal(str(preview.get("to_pay") or "0"))
    absorbs = Decimal(str(preview.get("vendor_absorbs") or "0"))
    return {
        "kind": amendment.kind,
        "kind_label": KIND_LABEL.get(amendment.kind, amendment.kind),
        "reasons": list(amendment.reasons),
        "lines": lines,
        "shipping": shipping,
        "address_changed": any(c.get("kind") == am.ChangeAddress.kind for c in amendment.changes),
        "gross_before": _money(preview.get("before", {}).get("gross_total")),
        "gross_after": _money(preview.get("after", {}).get("gross_total")),
        "refund": _money(refund) if refund > 0 else "",
        "to_pay": _money(to_pay) if to_pay > 0 else "",
        "vendor_absorbs": _money(absorbs) if absorbs > 0 else "",
        "commission_delta": _money(preview.get("commission_delta")),
        "passkey": amendments.amendment_passkey(amendment) is not None,
    }


class AmendmentPage(ManagerView):
    """The amendment page of one package, in the shop or the platform space."""

    template_name = "manager/amendment.html"
    namespace: ClassVar[str] = "manager:shop"

    @property
    def staff(self) -> bool:
        return self.manager.space is Space.PLATFORM

    def ctx(self) -> AuditContext:
        found = audit_context(self.request)
        return found if self.staff else replace(found, source=AuditLog.Source.VENDOR)

    def sub_order(self) -> VendorOrder:
        return get_object_or_404(
            self.scoped(VendorOrder).select_related("order", "vendor"),
            public_id=self.kwargs["public_id"],
        )

    def url(self, name: str, sub: VendorOrder, *extra: Any) -> str:
        return reverse(f"{self.namespace}:{name}", args=[sub.public_id, *extra])

    def page_url(self, sub: VendorOrder) -> str:
        return self.url("order_amend", sub)

    def draft_side(self, draft: OrderAmendment) -> bool:
        """Whether the open draft is this side's: the shop's for the shop, staff's for staff."""
        return (draft.initiator in am.STAFF_INITIATORS) == self.staff

    def cancel_form(self, data: Any = None) -> CancelPackageForm | ShopCancelForm:
        return (
            CancelPackageForm(data, prefix="cancel")
            if self.staff
            else ShopCancelForm(data, prefix="cancel")
        )

    def build_form(self, sub: VendorOrder, data: Any = None, initial: Any = None) -> AmendmentForm:
        version = versions.current_version(sub)
        return AmendmentForm(
            data,
            initial=initial,
            lines=lines_of(version.snapshot),
            carriers=amendments.available_carriers(sub),
            current_carrier=str(version.snapshot.get("shipping", {}).get("key") or ""),
            address=versions.delivery_address(sub, version),
            staff=self.staff,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        sub = self.sub_order()
        order = sub.order
        version = versions.current_version(sub) if sub.current_version_id else None
        draft = amendments.open_amendment(sub)
        # The same rule as the service (`_lock_amendable`): `amendments.staff_actionable`. A
        # frozen shop or a registered withdrawal leaves the forms on the page, disabled, with the
        # reason (ORD.30); the other refusals (not paid, shipped) hide them.
        verdict = amendments.staff_actionable(sub) if self.staff else None
        if verdict is not None:
            amendable = version is not None and (
                verdict.actionable or verdict.block_reason is not None
            )
        else:
            amendable = (
                version is not None
                and sub.state in am.AMENDABLE_VENDOR_ORDER_STATES
                and order.state in PAID_OR_LATER
            )
        blocked = verdict.block_reason if verdict is not None else None
        mine = draft is not None and self.draft_side(draft)
        context.update(
            title=_("Change the order"),
            sub=sub,
            number=order.number,
            staff=self.staff,
            order_url=(
                reverse("manager:shop:order", args=[sub.public_id])
                if not self.staff
                else self.url("order", sub)
            ),
            version_number=version.number if version else None,
            lines=version_lines(version.snapshot) if version else [],
            total=_money(version.gross_total) if version else "",
            show_address=amendable and order.privacy_status == "active",
            address=address_lines(versions.delivery_address(sub, version)) if version else [],
            amendable=amendable,
            blocked=BLOCK_LABELS.get(blocked, "") if blocked else "",
            hold_until=sub.ship_hold_until,
            draft=draft,
            draft_mine=mine,
            draft_by=INITIATOR_LABEL.get(draft.initiator, "") if draft else "",
            preview=preview_context(draft) if draft is not None else None,
            apply_url=self.url("amendment_apply", sub, draft.public_id) if draft else "",
            discard_url=self.url("amendment_discard", sub, draft.public_id) if draft else "",
            form_url=self.page_url(sub),
        )
        if amendable and (draft is None or (mine and draft.state == AmendmentState.DRAFT)):
            if "form" not in context:
                initial = (
                    initial_from(draft.changes, draft.customer_request or {})
                    | {"reason": draft.reason, "staff_note": draft.staff_note}
                    if draft is not None
                    else None
                )
                context["form"] = self.build_form(sub, initial=initial)
        else:
            context.pop("form", None)
        if amendable and "cancel_form" not in context:
            context["cancel_form"] = self.cancel_form()
        context["cancel_url"] = self.url("order_cancel", sub)
        return context

    def refused(self, message: str, status: int = 409, **extra: Any) -> HttpResponse:
        return self.render_to_response(
            self.get_context_data(action_error=message, **extra), status=status
        )

    def prompt(self, sub: VendorOrder, error: amendments.PasskeyRequired, url: str) -> HttpResponse:
        """The passkey prompt of a critical change (CORE.25): the "Do it once" ceremony mints the
        single-use grant bound to the change's digest, then the POST is replayed. Never cached."""
        request = self.request
        shop = sub.vendor
        levels.record_prompt(
            request, error.action, GROUP_CRITICAL, "passkey_required", shop=levels.shop_key(shop)
        )
        can_confirm = levels.may_confirm(request.user, error.action, shop)
        if can_confirm and not levels.may_elevate(request.user):
            levels.offer_shop_confirmation(request, error.action, error.digest, shop)
        replay = [
            (key, value)
            for key in request.POST
            if key != "csrfmiddlewaretoken"
            for value in request.POST.getlist(key)
        ]
        context = super().get_context_data()
        context.update(
            sub=sub,
            number=sub.order.number,
            message=str(error),
            action=error.action,
            action_label=ACTION_LABEL.get(error.action, error.action),
            digest=error.digest,
            can_confirm=can_confirm,
            has_passkey=levels.has_passkey(request.user),
            replay_url=url,
            replay_fields=replay,
            back_url=self.page_url(sub),
            title=_("Confirm with your passkey"),
        )
        response = self.response_class(
            request=request,
            template=["manager/amendment_passkey.html"],
            context=context,
            status=403,
        )
        add_never_cache_headers(response)
        return response

    @requires_level_method("orders_in_progress")  # everyday; critical steps ask their passkey
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        sub = self.sub_order()
        actor = _actor(self)
        if sub.current_version_id is None:
            return self.refused(_("This order is not paid yet, so it cannot be changed here."))
        form = self.build_form(sub, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        draft = amendments.open_amendment(sub)
        note = str(form.cleaned_data.get("staff_note") or "")
        try:
            if draft is not None and draft.state == AmendmentState.DRAFT:
                amendments.revise_amendment(
                    draft,
                    form.changes(),
                    actor=actor,
                    ctx=self.ctx(),
                    reason=form.cleaned_data["reason"],
                    staff_note=note,
                    customer_request=form.customer_request() or {},
                )
            else:
                amendments.draft_amendment(
                    sub,
                    form.changes(),
                    actor=actor,
                    reason=form.cleaned_data["reason"],
                    ctx=self.ctx(),
                    staff_note=note,
                    customer_request=form.customer_request(),
                )
        except (amendments.AmendmentRefused, OrdersFrozen) as error:
            return self.refused(str(error), form=form)
        except PermissionDenied:
            raise Http404("No such order.") from None
        messages.success(
            request,
            _("The change is computed. Check the preview, then apply it or discard it."),
        )
        return HttpResponseRedirect(self.page_url(sub))


class AmendmentApplyView(AmendmentPage):
    """POST: apply the open draft (or answer with the passkey prompt)."""

    http_method_names = ["post", "options"]

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        sub = self.sub_order()
        actor = _actor(self)
        amendment = get_object_or_404(
            OrderAmendment.objects.select_related("from_version", "vendor_order__vendor"),
            vendor_order=sub,
            public_id=self.kwargs["amendment"],
        )
        try:
            amendments.apply_amendment(
                amendment,
                actor=actor,
                ctx=self.ctx(),
                passkey_session=request.session.session_key or "",
            )
        except amendments.PasskeyRequired as error:
            return self.prompt(sub, error, request.get_full_path())
        except (amendments.AmendmentRefused, OrdersFrozen) as error:
            return self.refused(str(error))
        except PermissionDenied:
            raise Http404("No such order.") from None
        if amendment.state == AmendmentState.SUPERSEDED:
            return self.refused(
                _(
                    "The package changed since this draft was made, so the draft was closed. "
                    "Start a new one."
                )
            )
        refund = Decimal(str(amendment.preview.get("refund") or "0"))
        if refund > 0:
            text = _(
                "The change is applied. %(amount)s is refunded to the customer, credited within "
                "5 to 10 business days depending on the card issuer, and in any case within 14 "
                "days."
            ) % {"amount": _money(refund)}
        else:
            text = _("The change is applied. No money moves.")
        messages.success(request, text)
        return HttpResponseRedirect(self.page_url(sub))


class AmendmentDiscardView(AmendmentPage):
    """POST: discard the open draft."""

    http_method_names = ["post", "options"]

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        sub = self.sub_order()
        amendment = get_object_or_404(
            OrderAmendment, vendor_order=sub, public_id=self.kwargs["amendment"]
        )
        if not self.draft_side(amendment):
            return self.refused(_("Only the side that started this draft may discard it."))
        try:
            amendments.discard_amendment(amendment, actor=_actor(self), ctx=self.ctx())
        except amendments.AmendmentRefused as error:
            return self.refused(str(error))
        except PermissionDenied:
            raise Http404("No such order.") from None
        messages.success(request, _("The draft is discarded. Nothing changed in the order."))
        return HttpResponseRedirect(self.page_url(sub))


# --- the shop space ------------------------------------------------------------------------------


class ShopAmendView(AmendmentPage):
    section = "shop.orders"


class ShopAmendmentApplyView(AmendmentApplyView):
    section = "shop.orders"


class ShopAmendmentDiscardView(AmendmentDiscardView):
    section = "shop.orders"


# --- the platform space ------------------------------------------------------------------------


BLOCK_LABELS = {
    amendments.BLOCK_VENDOR_FROZEN: gettext_lazy("Shop frozen"),
    amendments.BLOCK_WITHDRAWAL: gettext_lazy("Withdrawal registered"),
}


class PlatformOrdersView(ManagerView):
    """Support and the platform owner find a paid order by its number (any package, shipped or
    closed included) and see, oldest first, the packages on which an ORD.22 action is still
    possible (ORD.30: `amendments.staff_actionable`, the rule the actions use). The list shows no
    buyer contact data: number, shop, state, date, total and the open amendment."""

    section = PLATFORM_SECTION
    template_name = "manager/platform/orders.html"
    paginate_by: ClassVar[int] = 25

    def _number_rows(self, number: str) -> list[dict[str, str]]:
        found = (
            self.scoped(VendorOrder)
            .select_related("order", "vendor")
            .filter(order__number=number)
            .order_by("id")
        )
        return [
            {
                "vendor": sub.vendor.display_name,
                "state": sub.get_state_display(),
                "url": reverse("manager:platform:order", args=[sub.public_id]),
            }
            for sub in found
        ]

    def _filters(self, rows: Any) -> tuple[Any, dict[str, str]]:
        """Narrow `rows` by `?shop=`, `?state=`, `?placed_from=` and `?placed_to=`; a value that
        is not valid narrows nothing. Returns the rows and the accepted values."""
        get = self.request.GET
        used: dict[str, str] = {}
        shop = get.get("shop", "")
        try:
            shop_id = uuid.UUID(shop)
        except ValueError:
            shop_id = None
        if shop_id:
            rows = rows.filter(vendor__public_id=shop_id)
            used["shop"] = shop
        state = get.get("state", "")
        if state in am.AMENDABLE_VENDOR_ORDER_STATES:
            rows = rows.filter(state=state)
            used["state"] = state
        for name, lookup in (
            ("placed_from", "order__created_at__date__gte"),
            ("placed_to", "order__created_at__date__lte"),
        ):
            try:
                day = dt.date.fromisoformat(get.get(name, ""))
            except ValueError:
                continue
            rows = rows.filter(**{lookup: day})
            used[name] = day.isoformat()
        return rows, used

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        number = self.request.GET.get("number", "").strip()[:40]
        rows = self._number_rows(number) if number else []
        open_amendments = OrderAmendment.objects.filter(state__in=OPEN_AMENDMENT_STATES).only(
            "vendor_order_id", "state"
        )
        base_rows = amendments.staff_actionable_packages(self.scoped(VendorOrder))
        actionable, used = self._filters(
            base_rows.select_related("order", "vendor").prefetch_related(
                Prefetch("amendments", queryset=open_amendments, to_attr="open")
            )
        )
        page = Paginator(actionable.order_by("order__created_at", "id"), self.paginate_by).get_page(
            self.request.GET.get("page")
        )
        # A blocked package (frozen shop, registered withdrawal) stays listed with its reason.
        verdicts = amendments.staff_actionability(list(page.object_list))
        base = urlencode(used)
        lead = f"?{base}&" if base else "?"
        context.update(
            number=number,
            rows=rows,
            searched=bool(number),
            packages=[
                {
                    "number": sub.order.number,
                    "vendor": sub.vendor.display_name,
                    "state": sub.get_state_display(),
                    "placed": sub.order.created_at,
                    "total": _money(sub.gross_total),
                    "amendment": sub.open[0].get_state_display() if sub.open else "",
                    "blocked": BLOCK_LABELS.get(verdicts[sub.pk].block_reason or "", ""),
                    "url": reverse("manager:platform:order", args=[sub.public_id]),
                }
                for sub in page.object_list
            ],
            page_obj=page,
            previous_url=f"{lead}page={page.previous_page_number()}" if page.has_previous() else "",
            next_url=f"{lead}page={page.next_page_number()}" if page.has_next() else "",
            shop_choices=[
                (str(public_id), name)
                for public_id, name in base_rows.order_by("vendor__display_name")
                .values_list("vendor__public_id", "vendor__display_name")
                .distinct()
            ],
            state_choices=[
                (key, label)
                for key, label in VendorOrderState.choices
                if key in am.AMENDABLE_VENDOR_ORDER_STATES
            ],
            filter_values={
                name: used.get(name, "") for name in ("shop", "state", "placed_from", "placed_to")
            },
            # ORD.22: package cancellations per shop and cause; a shop's own "the customer asked"
            # drops the refund fee on its word alone, so those come first and are marked.
            cancellation_days=amendments.CANCELLATION_COUNT_DAYS,
            cancellations=amendments.cancellation_counts(),
        )
        return context


class PlatformAmendView(AmendmentPage):
    section = PLATFORM_SECTION
    namespace = "manager:platform"

    def page_url(self, sub: VendorOrder) -> str:
        return self.url("order", sub)


class PlatformAmendmentApplyView(AmendmentApplyView):
    section = PLATFORM_SECTION
    namespace = "manager:platform"

    def page_url(self, sub: VendorOrder) -> str:
        return self.url("order", sub)


class PlatformAmendmentDiscardView(AmendmentDiscardView):
    section = PLATFORM_SECTION
    namespace = "manager:platform"

    def page_url(self, sub: VendorOrder) -> str:
        return self.url("order", sub)


class CancelView(AmendmentPage):
    """POST: cancel the package with a full refund. The shop (owner, members with ORDERS) gives
    its own fault or the customer's request, which it records; platform staff (ORD.22) give the
    cause and the mandatory note. The passkey is always asked."""

    http_method_names = ["post", "options"]

    @requires_level_method("orders_in_progress")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        sub = self.sub_order()
        form = self.cancel_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(cancel_form=form), status=400)
        data = form.cleaned_data
        try:
            amendments.cancel_package(
                sub,
                actor=_actor(self),
                reason=data["reason"],
                cause=data["cause"],
                staff_note=str(data.get("staff_note") or ""),
                customer_request=(
                    form.customer_request() if isinstance(form, ShopCancelForm) else None
                ),
                ctx=self.ctx(),
                passkey_session=request.session.session_key or "",
            )
        except amendments.PasskeyRequired as error:
            return self.prompt(sub, error, request.get_full_path())
        except (amendments.AmendmentRefused, OrdersFrozen) as error:
            return self.refused(str(error), cancel_form=form)
        except PermissionDenied:
            raise Http404("No such order.") from None
        if data["cause"] == amendments.CANCEL_VENDOR_FAULT:
            text = _(
                "The package is cancelled and the customer is refunded in full, within 14 days "
                "at the latest. The refund fee is charged to the shop."
            )
        else:
            text = _(
                "The package is cancelled and the customer is refunded in full, within 14 days "
                "at the latest. No refund fee is charged to the shop."
            )
        messages.success(request, text)
        return HttpResponseRedirect(self.page_url(sub))


class ShopCancelView(CancelView):
    section = "shop.orders"


class PlatformCancelView(CancelView):
    section = PLATFORM_SECTION
    namespace = "manager:platform"

    def page_url(self, sub: VendorOrder) -> str:
        return self.url("order", sub)


class AmendmentSettingsView(ManagerView):
    """The three amendment settings in force, each with a one-line plain explanation; they are
    read from the same configuration the services use (spec 07, Settings)."""

    section = SETTINGS_SECTION
    template_name = "manager/platform/amendment_settings.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rules = amendments.amendment_settings()
        context["settings_rows"] = [
            {
                "label": _("Time to answer a proposed change"),
                "value": _("%(n)s hours") % {"n": rules.answer_hours},
                "explanation": _(
                    "How long the customer has to accept or decline a proposed change; after "
                    "that the order stays as it was."
                ),
            },
            {
                "label": _("Comparable delivery delay"),
                "value": _("%(n)s days") % {"n": rules.comparable_delay_days},
                "explanation": _(
                    "How many days later a new carrier may deliver and still count as a minor "
                    "change that applies at once; a slower one needs the customer's acceptance."
                ),
            },
            {
                "label": _("Shipping hold after an unavailable item"),
                "value": _("%(n)s hours") % {"n": rules.cancel_hold_hours},
                "explanation": _(
                    "After an item is removed as unavailable, the seller waits this long before "
                    "shipping, so the customer can cancel the rest of the package."
                ),
            },
        ]
        return context

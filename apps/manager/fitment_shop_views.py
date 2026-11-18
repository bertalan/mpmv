"""Manager shop space: the Fits pages of a shop (FIT.3, spec 17 §17.3).

A list of the shop's own products with the declaration state and a bulk action, the Fits page of
one product (mode, rows, copy from another product), the proposal of a missing node and the
shop's data-quality report. Thin adapters over `apps.fitment.services`, which hold the rules, the
`ChangeSet` and the `AuditLog`. A change shows its dry-run `ChangeSet` first and is written only
on confirmation. Every product is found through `scoped()`: another shop's product is a 404.
"""

from dataclasses import replace
from typing import Any, ClassVar

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, QuerySet
from django.forms import formset_factory
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.decorators import requires_level_method
from apps.catalog.models import Product, ProductTranslation
from apps.core import translations
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import (
    DeclarationMode,
    FitmentNodeProposal,
    NodeStatus,
    ProductFitment,
)
from apps.fitment.services import FitmentError, RowInput

from .fitment_shop_forms import (
    MODE_CHOICES,
    MODE_LABELS,
    CopyForm,
    ModeForm,
    ProposalForm,
    RowForm,
    node_choices,
    note_choices,
    other_products,
    row_inputs,
)
from .fitment_views import echo
from .lists import BulkAction, Column, ListFilter, ManagerListView
from .navigation import vendor_action
from .views import ManagerView

SECTION = "shop.fitment"
ADD_ACTION = "add_fitment"
EXTRA_ROWS = 3


def _shop(view: Any, request: HttpRequest) -> object | None:
    vendor: object | None = view.manager.vendor
    return vendor


def _ctx(request: HttpRequest) -> Any:
    return replace(audit_context(request), source=AuditLog.Source.VENDOR)


def _title(product: Product) -> str:
    by_language = {t.language: t.title for t in product.translations.all()}
    return by_language.get(translations.source_language()) or next(iter(by_language.values()), "")


def _row_formset(
    nodes: Any, notes: Any, data: Any = None, initial: Any = None, extra: int = EXTRA_ROWS
) -> Any:
    factory = formset_factory(RowForm, extra=extra, can_delete=True)
    return factory(
        data, initial=initial, prefix="rows", form_kwargs={"nodes": nodes, "notes": notes}
    )


class DeclarationFilter(ListFilter):
    def apply[M: Any](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        if not self.accepts(value):
            return rows
        if value == DeclarationMode.UNDECLARED:
            return rows.exclude(
                fitment_state__mode__in=[DeclarationMode.SPECIFIC, DeclarationMode.UNIVERSAL]
            )
        return rows.filter(fitment_state__mode=value)


class SearchFilter(ListFilter):
    def apply[M: Any](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        term = value.strip()
        return rows.filter(sku__icontains=term) if term else rows


def _sku_cell(product: Product) -> Any:
    url = reverse("manager:shop:fitment_product", args=[product.public_id])
    return format_html('<a href="{}">{}</a>', url, product.sku)


def _state_cell(product: Product) -> str:
    state = getattr(product, "fitment_state", None)
    mode = state.mode if state else DeclarationMode.UNDECLARED
    return str(MODE_LABELS[mode])


def _noop(objects: list[Product], *, ctx: Any, dry_run: bool) -> ChangeSet:  # pragma: no cover
    raise NotImplementedError


class FitmentList(ManagerListView):
    """The shop's products and what each declares; the bulk action asks for the rows first."""

    section = SECTION
    template_name = "manager/shop/fitment_list.html"
    paginate_by = 25
    columns = (
        Column(gettext_lazy("SKU"), _sku_cell),
        Column(gettext_lazy("Title"), _title),
        Column(gettext_lazy("Category"), lambda p: str(p.category) if p.category else "—"),
        Column(gettext_lazy("Declaration"), _state_cell),
    )
    filters = (
        SearchFilter("q", gettext_lazy("Search"), (), lookup="", text=True),
        DeclarationFilter(
            "declaration",
            gettext_lazy("Declaration"),
            tuple((v, str(label)) for v, label in MODE_CHOICES),
            lookup="",
        ),
    )
    bulk_actions: ClassVar[tuple[BulkAction, ...]] = (
        BulkAction(
            ADD_ACTION,
            gettext_lazy("Add fitment to the selected products"),
            _noop,
            allowed=vendor_action("products"),
        ),
    )

    def get_queryset(self) -> QuerySet[Product]:
        rows: QuerySet[Product] = (
            Product.objects.select_related("category", "fitment_state")
            .prefetch_related(
                Prefetch(
                    "translations",
                    queryset=ProductTranslation.objects.only("product_id", "language", "title"),
                )
            )
            .order_by("sku")
        )
        return rows

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["title"] = _("Fitment")
        return context

    @requires_level_method("bulk_edit", shop=_shop)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.POST.get("action") != ADD_ACTION or not self.available_actions():
            raise Http404("No such action.")
        ids = request.POST.getlist("selected")
        if not ids:
            context = self.get_context_data(**kwargs)
            context["selection_error"] = _("Select at least one row.")
            return self.render_to_response(context)
        products = self.selected(ids)
        nodes, notes = node_choices(), note_choices()
        context = ManagerView.get_context_data(self)
        context.update(
            title=_("Add fitment to the selected products"),
            selected=ids,
            count=len(products),
            list_url=self.list_url(),
            action=ADD_ACTION,
        )
        if "rows-TOTAL_FORMS" not in request.POST:
            context["formset"] = _row_formset(nodes, notes)
            return self._render(context)
        formset = _row_formset(nodes, notes, data=request.POST)
        context["formset"] = formset
        if not formset.is_valid():
            return self._render(context)
        rows = row_inputs(formset.forms)
        confirmed = request.POST.get("confirm") == "1"
        try:
            changes = services.add_product_fitment(
                products, rows, ctx=_ctx(request), dry_run=not confirmed
            )
        except (FitmentError, ValidationError) as error:
            context["error"] = "; ".join(getattr(error, "messages", [str(error)]))
            return self._render(context)
        if confirmed:
            messages.success(request, _("The fitment is added to the selected products."))
            return HttpResponseRedirect(self.list_url())
        context.update(changes=changes.changes, echo=echo(request))
        return self._render(context)

    def _render(self, context: dict[str, Any]) -> HttpResponse:
        return self.response_class(
            request=self.request,
            template=["manager/shop/fitment_bulk.html"],
            context=context,
            using=self.template_engine,
        )


class FitmentProductView(ManagerView):
    """The Fits tab of one product: the mode, the rows, copy from another product."""

    section = SECTION
    template_name = "manager/shop/fitment_product.html"

    def product(self) -> Product:
        rows = self.scoped(
            Product.objects.select_related("category", "fitment_state").prefetch_related(
                "translations"
            )
        )
        return get_object_or_404(rows, public_id=self.kwargs["public_id"])

    def _choices(self, product: Product) -> tuple[Any, Any, Any]:
        existing = list(
            ProductFitment.objects.filter(product=product)
            .exclude(node__status=NodeStatus.ACTIVE)
            .select_related("node")
        )
        nodes = node_choices() + [
            (str(r.node.public_id), f"{r.node.name} ({r.node.get_status_display()})")
            for r in existing
        ]
        return nodes, note_choices(), existing

    def _initial(self, product: Product) -> list[dict[str, Any]]:
        return [
            {
                "node": str(r.node.public_id),
                "year_from": r.year_from,
                "year_to": r.year_to,
                "is_exclusion": r.is_exclusion,
                "note": str(r.note_id) if r.note_id else "",
            }
            for r in ProductFitment.objects.filter(product=product).select_related("node")
        ]

    def _copy_form(self, product: Product, data: Any = None) -> CopyForm:
        return CopyForm(data, sources=other_products(product, self.scoped(Product)))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        product = self.product()
        nodes, notes, _existing = self._choices(product)
        state = getattr(product, "fitment_state", None)
        mode = state.mode if state else DeclarationMode.UNDECLARED
        context.setdefault("mode_form", ModeForm(initial={"mode": mode}))
        context.setdefault("formset", _row_formset(nodes, notes, initial=self._initial(product)))
        context.setdefault("copy_form", self._copy_form(product))
        context.update(
            product=product,
            title=_title(product) or product.sku,
            list_url=reverse("manager:shop:fitment"),
            propose_url=reverse("manager:shop:fitment_proposals"),
            state_label=_state_cell(product),
        )
        return context

    @requires_level_method("catalogue_edit", shop=_shop)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        product = self.product()
        confirmed = request.POST.get("confirm") == "1"
        action = request.POST.get("action", "save")
        ctx = _ctx(request)
        nodes, notes, _existing = self._choices(product)
        mode_form = ModeForm(request.POST)
        formset = _row_formset(nodes, notes, data=request.POST)
        copy_form = self._copy_form(product, request.POST if action == "copy" else None)
        bound: dict[str, Any] = {"mode_form": mode_form, "formset": formset, "copy_form": copy_form}
        warnings: list[str] = []
        try:
            if action == "copy":
                if not copy_form.is_valid():
                    return self.render_to_response(self.get_context_data(**bound))
                source = get_object_or_404(
                    self.scoped(Product), public_id=copy_form.cleaned_data["source"]
                )
                changes = services.copy_product_fitment(
                    source, product, ctx=ctx, dry_run=not confirmed
                )
            elif action == "save":
                if not (mode_form.is_valid() and formset.is_valid()):
                    return self.render_to_response(self.get_context_data(**bound))
                mode = mode_form.cleaned_data["mode"]
                rows: list[RowInput] = row_inputs(formset.forms)
                warnings = services.product_fitment_warnings(mode, rows)
                changes = services.set_product_fitment(
                    product,
                    mode,
                    rows,
                    ProductFitment.Source.MANUAL,
                    ctx=ctx,
                    dry_run=not confirmed,
                )
            else:
                raise Http404("No such action.")
        except (FitmentError, ValidationError) as error:
            context = self.get_context_data(**bound)
            context["error"] = "; ".join(getattr(error, "messages", [str(error)]))
            return self.render_to_response(context)
        if confirmed:
            messages.success(request, _("The fitment of this product is saved."))
            return HttpResponseRedirect(request.path)
        context = self.get_context_data(**bound)
        context.update(changes=changes.changes, echo=echo(request), warnings=warnings)
        return self.render_to_response(context)


class FitmentProposalsView(ManagerView):
    """A shop proposes an item it cannot find (spec 17 §17.3.2) and sees what it proposed."""

    section = SECTION
    template_name = "manager/shop/fitment_proposals.html"

    def _form(self, data: Any = None) -> ProposalForm:
        return ProposalForm(data, parents=node_choices(skip_last_level=True))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        proposals = self.scoped(FitmentNodeProposal).select_related("node").order_by("-created_at")
        context.update(
            proposals=[
                {"name": p.node.name, "status": p.node.get_status_display()} for p in proposals
            ],
            pending=services.pending_proposals(self.manager.vendor) if self.manager.vendor else 0,
            limit=services.max_pending_proposals(),
            list_url=reverse("manager:shop:fitment"),
        )
        context.setdefault("form", self._form())
        return context

    @requires_level_method("catalogue_edit", shop=_shop)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor = self.manager.vendor
        if vendor is None:
            raise Http404("No vendor selected.")
        form = self._form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            services.propose_node_for_vendor(
                vendor,
                name=form.cleaned_data["name"],
                parent=form.parent_node(),
                ctx=_ctx(request),
            )
        except (FitmentError, ValidationError) as error:
            form.add_error(None, "; ".join(getattr(error, "messages", [str(error)])))
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, _("Your proposal is sent. It stays hidden until it is reviewed."))
        return HttpResponseRedirect(request.path)


class FitmentReportView(ManagerView):
    """The shop's data-quality report."""

    section = SECTION
    template_name = "manager/shop/fitment_report.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.manager.vendor
        assert vendor is not None  # noqa: S101  # the section needs a vendor
        report = services.vendor_quality_report(vendor)

        def link(items: list[Product]) -> list[dict[str, str]]:
            return [
                {"sku": p.sku, "url": reverse("manager:shop:fitment_product", args=[p.public_id])}
                for p in items
            ]

        context.update(
            undeclared=link(report.undeclared),
            pending_rows=link(report.pending_rows),
            suspicious=link(report.suspicious),
            list_url=reverse("manager:shop:fitment"),
            title=_("Fitment report"),
        )
        return context

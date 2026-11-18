"""Manager platform space: the Catalog section for the Catalog and moderation role (CAT.11).

Thin adapters over `apps.catalog.services` (`set_products_status` for the bulk publish, retire and
unpublish, `save_product` for the audited fields) and `apps.catalog.engine` (the per-language
translation state). The list filters by vendor, status, category, translation status and a text
search; a bulk action shows its dry-run ChangeSet first and writes only on confirmation, behind the
session level of CORE.25. Finance and Support are not in the matrix row (404); the platform owner
reads.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet, Subquery
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.decorators import requires_level_method
from apps.catalog import engine, services
from apps.catalog.models import Category, Product, ProductStatus, ProductTranslation
from apps.core import translations
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet
from apps.core.i18n import configured_languages
from apps.vendors.models import Vendor

from .fitment_views import WriteGate, echo, writer
from .lists import BulkAction, BulkRun, Column, ListFilter, ManagerListView
from .views import ManagerView

SECTION = "platform.products"
PUBLISHED = "published"


class TextFilter(ListFilter):
    """Free-text search over the SKU and the titles, in every language."""

    def apply[M: Any](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        term = value.strip()
        if not term:
            return rows
        titles = ProductTranslation.objects.filter(product=OuterRef("pk"), title__icontains=term)
        return rows.filter(Q(sku__icontains=term) | Exists(titles))


def _stale_rows() -> QuerySet[ProductTranslation]:
    """Non-source texts of the product made from another source text than the current one."""
    source = ProductTranslation.objects.filter(
        product=OuterRef(OuterRef("pk")), language=translations.source_language()
    ).values("source_hash")[:1]
    return (
        ProductTranslation.objects.filter(product=OuterRef("pk"))
        .exclude(language=translations.source_language())
        .exclude(source_hash=Subquery(source))
    )


class TranslationFilter(ListFilter):
    """`complete`, `incomplete`, `stale` or `missing:<language>`."""

    def apply[M: Any](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        if not self.accepts(value):
            return rows
        stale = Exists(_stale_rows())
        if value == "stale":
            return rows.filter(stale)
        if value.startswith("missing:"):
            language = value.split(":", 1)[1]
            return rows.exclude(
                Exists(ProductTranslation.objects.filter(product=OuterRef("pk"), language=language))
            )
        complete = Q()
        for language in configured_languages():
            complete &= Q(
                Exists(
                    ProductTranslation.objects.filter(
                        product=OuterRef("pk"), language=language, status=PUBLISHED
                    )
                )
            )
        complete &= ~Q(stale)
        return rows.filter(complete) if value == "complete" else rows.exclude(complete)


def _status_run(status: str) -> BulkRun:
    def run(objects: list[Product], *, ctx: Any, dry_run: bool) -> ChangeSet:
        return services.set_products_status(objects, status, ctx=ctx, dry_run=dry_run)

    return run


def _states(product: Product) -> dict[str, str]:
    rows = [(t.language, t.status, t.source_hash) for t in product.translations.all()]
    return engine.translation_states(rows, configured_languages(), translations.source_language())


def _title(product: Product) -> str:
    by_language = {t.language: t.title for t in product.translations.all()}
    return by_language.get(translations.source_language()) or next(iter(by_language.values()), "")


def _sku_cell(product: Product) -> Any:
    url = reverse("manager:platform:catalog_product", args=[product.public_id])
    return format_html('<a href="{}">{}</a>', url, product.sku)


def _states_cell(product: Product) -> str:
    states = _states(product)
    return ", ".join(f"{language}: {_state_label(state)}" for language, state in states.items())


def _state_label(state: str) -> str:
    labels = {
        "missing": _("missing"),
        "stale": _("outdated"),
        "machine": _("draft"),
        "reviewed": _("reviewed"),
        "published": _("published"),
    }
    return str(labels.get(state, state))


class ProductList(ManagerListView):
    section = SECTION
    template_name = "manager/platform/products.html"
    paginate_by = 25
    columns = (
        Column(gettext_lazy("SKU"), _sku_cell),
        Column(gettext_lazy("Title"), _title),
        Column(gettext_lazy("Shop"), lambda p: p.vendor.display_name),
        Column(gettext_lazy("Status"), lambda p: p.get_status_display()),
        Column(gettext_lazy("Category"), lambda p: str(p.category) if p.category else "—"),
        Column(gettext_lazy("Translations"), _states_cell),
    )
    bulk_actions: ClassVar[Sequence[BulkAction]] = (
        BulkAction(
            "publish", gettext_lazy("Publish"), _status_run(PUBLISHED), allowed=writer(SECTION)
        ),
        BulkAction(
            "unpublish",
            gettext_lazy("Unpublish (back to draft)"),
            _status_run(ProductStatus.DRAFT),
            allowed=writer(SECTION),
        ),
        BulkAction(
            "retire",
            gettext_lazy("Retire (page live, not purchasable)"),
            _status_run(ProductStatus.RETIRED),
            allowed=writer(SECTION),
        ),
    )
    bulk_errors = (ValidationError,)

    def get_filters(self) -> Sequence[ListFilter]:
        if not hasattr(self, "_filters"):
            self._filters = self._build_filters()
        return self._filters

    def _build_filters(self) -> Sequence[ListFilter]:
        vendors = tuple(
            (str(public_id), name)
            for public_id, name in Vendor.objects.for_actor(self.manager.user)
            .order_by("display_name")
            .values_list("public_id", "display_name")
        )
        categories = tuple((str(c.public_id), str(c)) for c in Category.objects.order_by("path"))
        translation_choices = (
            ("complete", str(_("Complete"))),
            ("incomplete", str(_("Incomplete"))),
            ("stale", str(_("Outdated"))),
            *(
                (f"missing:{code}", str(_("Missing in %(language)s")) % {"language": code})
                for code in configured_languages()
            ),
        )
        return (
            TextFilter("q", gettext_lazy("Search"), (), lookup="", text=True),
            ListFilter("vendor", gettext_lazy("Shop"), vendors, lookup="vendor__public_id"),
            ListFilter(
                "status",
                gettext_lazy("Status"),
                tuple((str(v), str(label)) for v, label in ProductStatus.choices),
                lookup="status",
            ),
            ListFilter(
                "category", gettext_lazy("Category"), categories, lookup="category__public_id"
            ),
            TranslationFilter(
                "translation", gettext_lazy("Translations"), translation_choices, lookup=""
            ),
        )

    def get_queryset(self) -> QuerySet[Product]:
        rows: QuerySet[Product] = (
            Product.objects.select_related("vendor", "category")
            .prefetch_related(
                Prefetch(
                    "translations",
                    queryset=ProductTranslation.objects.only(
                        "product_id", "language", "status", "source_hash", "title"
                    ),
                )
            )
            .order_by("-created_at", "pk")
        )
        return rows

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["title"] = _("Products")
        return context


class StaffFieldsForm(forms.Form):
    sales_mode = forms.ChoiceField(
        choices=Product.SalesMode.choices, label=gettext_lazy("Sales mode")
    )
    fiscal_category = forms.ChoiceField(
        choices=Product.FiscalCategory.choices, label=gettext_lazy("Fiscal category")
    )
    vendor_role = forms.ChoiceField(
        required=False,
        choices=[("", gettext_lazy("Use the shop's default")), *Vendor.ProductRole.choices],
        label=gettext_lazy("Shop role for this product"),
    )


class ProductDetailView(WriteGate, ManagerView):
    """The product as staff see it: facts, the state of each language and the audited fields."""

    section = SECTION
    write_section = SECTION
    template_name = "manager/platform/product.html"

    def product(self) -> Product:
        rows = self.scoped(
            Product.objects.select_related("vendor", "category").prefetch_related("translations")
        )
        return get_object_or_404(rows, public_id=self.kwargs["public_id"])

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        product = self.product()
        form = kwargs.get("form") or StaffFieldsForm(
            initial={
                "sales_mode": product.sales_mode,
                "fiscal_category": product.fiscal_category,
                "vendor_role": product.vendor_role,
            }
        )
        titles = {t.language: t.title for t in product.translations.all()}
        context.update(
            product=product,
            title=_title(product) or product.sku,
            form=form,
            can_write=writer(SECTION)(self.manager),
            languages=[
                {"code": code, "state": _state_label(state), "title": titles.get(code, "")}
                for code, state in _states(product).items()
            ],
            list_url=reverse("manager:platform:catalog_products"),
        )
        return context

    @requires_level_method("catalogue_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        product = self.product()
        form = StaffFieldsForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        for name, value in form.cleaned_data.items():
            setattr(product, name, value)
        confirmed = request.POST.get("confirm") == "1"
        try:
            changes = services.save_product(
                product, ctx=audit_context(request), dry_run=not confirmed
            )
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return HttpResponseRedirect(request.path)
        if confirmed:
            messages.success(request, _("The product is updated."))
            return HttpResponseRedirect(request.path)
        context = self.get_context_data(form=form)
        context.update(changes=changes.changes, echo=echo(request))
        return self.render_to_response(context)

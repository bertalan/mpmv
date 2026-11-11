"""Admin choosers for catalog terms, categories, brands, products and vendors (CMS.2 part 2/2,
CMS.10, CMS.3, CMS.9, spec 10 §10.2).

Wagtail's `ChooserViewSet` gives each model a chooser modal under `/admin/` and a StreamField
block (`apps.cms.blocks.TermChooserBlock`, `VendorChooserBlock` and so on). Editors never type a
URL.

- Terms are platform data with no vendor scope: every term is offered, labelled
  "taxonomy code: name" like the staff admin. Categories and brands (the product grid, CMS.10)
  are platform data too: all of them are offered, labelled as in the staff admin.
- Products (the blog's product cards, CMS.3, and the launch countdown, CMS.9): only listed
  products, published and of an active vendor (`apps.catalog.services.listed_products`), in the
  list and in the "chosen" steps alike, labelled by SKU. The list is searchable by name (any
  language), SKU, brand and vendor, and filterable by vendor, brand and category. A vendor
  member (who normally never reaches the admin) sees only their own vendors' products, so a
  crafted request for another vendor's product lists nothing and answers 404 (EVT.3, CORE.3).
- Vendors: only those with a public storefront (`apps.vendors.services.storefront_vendors`), in
  the list and in the "chosen" step alike, so a crafted request cannot pick another vendor.

The viewsets are registered in `wagtail_hooks`. Their widgets are not registered as the default
form widget of foreign keys to these models (`register_widget = False`): the staff admin and other
Wagtail forms keep theirs.
"""

from typing import Any

from django import forms
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from wagtail.admin.forms.choosers import BaseFilterForm
from wagtail.admin.ui.tables import Column
from wagtail.admin.views.generic.chooser import (
    ChooseResultsView,
    ChooseView,
    ChosenMultipleView,
    ChosenView,
)
from wagtail.admin.viewsets.chooser import ChooserViewSet

from apps.catalog.models import Brand, Category, Product, Term
from apps.catalog.services import listed_products
from apps.core import translations
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.services import storefront_vendors


class TermChooserViewSet(ChooserViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = Term
    icon = "tag"
    choose_one_text = _("Choose a term")
    choose_another_text = _("Choose another term")
    register_widget = False
    per_page = 50

    def get_object_list(self) -> QuerySet[Term]:
        return Term.objects.select_related("taxonomy").prefetch_related("translations")


class CategoryChooserViewSet(ChooserViewSet):  # type: ignore[misc]
    model = Category
    icon = "folder-open-inverse"
    choose_one_text = _("Choose a category")
    choose_another_text = _("Choose another category")
    register_widget = False
    per_page = 50

    def get_object_list(self) -> QuerySet[Category]:
        return Category.objects.prefetch_related("translations").order_by("path")


class BrandChooserViewSet(ChooserViewSet):  # type: ignore[misc]
    model = Brand
    icon = "tag"
    choose_one_text = _("Choose a brand")
    choose_another_text = _("Choose another brand")
    register_widget = False
    per_page = 50

    def get_object_list(self) -> QuerySet[Brand]:
        return Brand.objects.filter(status=Brand.Status.APPROVED).order_by("name")


SEARCH_WORDS_MAX = 8  # a longer query is cut: each word adds a pass over four columns


def own_vendor_ids(user: Any) -> list[int] | None:
    """The vendors a chooser user is limited to: their memberships (EVT.3, CORE.3), or None for
    an editor or staff member who belongs to no vendor and so sees every vendor.

    The middleware keeps vendor members out of the admin altogether; this is the second line, so
    a vendor member who reaches a chooser (a role mistake, a new route) still sees only their
    own products.
    """
    if not getattr(user, "is_authenticated", False):
        return []
    ids = list(VendorMember.objects.filter(user_id=user.pk).values_list("vendor_id", flat=True))
    return ids or None


def chooser_products(user: Any) -> QuerySet[Product]:
    """The products `user` may pick: listed ones, of their own vendors when they are a member."""
    products: QuerySet[Product] = listed_products()
    vendors = own_vendor_ids(user)
    return products if vendors is None else products.filter(vendor_id__in=vendors)


def _word_matches(word: str) -> Q:
    return (
        Q(sku__icontains=word)
        | Q(translations__title__icontains=word)
        | Q(brand__name__icontains=word)
        | Q(vendor__display_name__icontains=word)
    )


def search_products(products: QuerySet[Product], query: str) -> QuerySet[Product]:
    """Products whose name (in any language), SKU, brand or vendor contains every word of
    `query`. Narrowed through a subquery, so a to-many join never lists a product twice."""
    condition = Q()
    for word in query.split()[:SEARCH_WORDS_MAX]:
        condition &= _word_matches(word)
    if not condition:
        return products
    return products.filter(pk__in=Product.objects.filter(condition).values("pk"))


class ProductFilterForm(BaseFilterForm):  # type: ignore[misc]
    """The search box and the vendor, brand and category filters of the product chooser."""

    q = forms.CharField(
        label=_("Search term"),
        required=False,
        help_text=_("Type part of a product name, SKU, brand or vendor name."),
        widget=forms.TextInput(attrs={"placeholder": _("Name, SKU, brand or vendor")}),
    )

    def __init__(self, *args: Any, vendors: QuerySet[Vendor] | None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        attrs = {"data-chooser-modal-search-filter": True}
        if vendors is not None:
            self.fields["vendor"] = forms.ModelChoiceField(
                queryset=vendors,
                required=False,
                empty_label=_("All vendors"),
                label=_("Vendor"),
                help_text=_("Show only the products of one vendor."),
                widget=forms.Select(attrs=attrs),
            )
        self.fields["brand"] = forms.ModelChoiceField(
            queryset=Brand.objects.filter(status=Brand.Status.APPROVED).order_by("name"),
            required=False,
            empty_label=_("All brands"),
            label=_("Brand"),
            help_text=_("Show only the products of one brand."),
            widget=forms.Select(attrs=attrs),
        )
        self.fields["category"] = forms.ModelChoiceField(
            queryset=Category.objects.order_by("path"),
            required=False,
            empty_label=_("All categories"),
            label=_("Category"),
            help_text=_("Show only the products of one category and the categories below it."),
            widget=forms.Select(attrs=attrs),
        )

    def filter(self, objects: QuerySet[Product]) -> QuerySet[Product]:
        data = self.cleaned_data
        if data.get("vendor") is not None:
            objects = objects.filter(vendor=data["vendor"])
        if data.get("brand") is not None:
            objects = objects.filter(brand=data["brand"])
        if data.get("category") is not None:
            objects = objects.filter(category__path__startswith=data["category"].path)
        query = str(data.get("q") or "").strip()
        if query:
            objects = search_products(objects, query)
            self.is_searching = True
            self.search_query = query
        return objects


def _product_name(product: Product) -> str:
    shown = translations.resolve(product.translations.all(), get_language() or "en").translation
    return str(shown.title) if shown is not None else ""


def _product_vendor(product: Product) -> str:
    return str(product.vendor.display_name)


def _product_brand(product: Product) -> str:
    return str(product.brand) if product.brand is not None else ""


class ProductListMixin:
    """The product list of the chooser views: scoped to the user, searchable, with the product's
    name, vendor and brand next to its SKU."""

    request: HttpRequest

    def get_object_list(self) -> QuerySet[Product]:
        return (
            chooser_products(self.request.user)
            .select_related("vendor", "brand")
            .prefetch_related("translations")
            .order_by("sku")
        )

    def get_filter_form(self) -> ProductFilterForm:
        # A vendor member has one vendor to see: no vendor filter for them.
        unrestricted = own_vendor_ids(self.request.user) is None
        vendors = storefront_vendors().order_by("display_name") if unrestricted else None
        return ProductFilterForm(self.request.GET, vendors=vendors)

    @property
    def columns(self) -> list[Column]:
        return [
            super().columns[0],  # type: ignore[misc]  # the SKU, linked to the choice
            Column("name", label=_("Name"), accessor=_product_name),
            Column("vendor", label=_("Vendor"), accessor=_product_vendor),
            Column("brand", label=_("Brand"), accessor=_product_brand),
        ]


class ProductChooseView(ProductListMixin, ChooseView):  # type: ignore[misc]
    pass


class ProductChooseResultsView(ProductListMixin, ChooseResultsView):  # type: ignore[misc]
    pass


def _pk(value: Any) -> int:
    """A product id from a request; anything else is "no such product", never a server error."""
    text = str(value)
    if not text.isascii() or not text.isdigit():
        raise Product.DoesNotExist
    return int(text)


class ProductChosenView(ChosenView):  # type: ignore[misc]
    def get_object(self, pk: Any) -> Product:
        return chooser_products(self.request.user).get(pk=_pk(pk))


class ProductChosenMultipleView(ChosenMultipleView):  # type: ignore[misc]
    def get_objects(self, pks: Any) -> QuerySet[Product]:
        ids = [int(pk) for pk in pks if str(pk).isascii() and str(pk).isdigit()]
        return chooser_products(self.request.user).filter(pk__in=ids)


class ProductChooserViewSet(ChooserViewSet):  # type: ignore[misc]
    model = Product
    icon = "pick"
    choose_one_text = _("Choose a product")
    choose_another_text = _("Choose another product")
    register_widget = False
    choose_view_class = ProductChooseView
    choose_results_view_class = ProductChooseResultsView
    chosen_view_class = ProductChosenView
    chosen_multiple_view_class = ProductChosenMultipleView
    per_page = 50


class VendorChosenView(ChosenView):  # type: ignore[misc]
    def get_object(self, pk: Any) -> Vendor:
        return storefront_vendors().get(pk=pk)


class VendorChooserViewSet(ChooserViewSet):  # type: ignore[misc]
    model = Vendor
    icon = "group"
    choose_one_text = _("Choose a vendor")
    choose_another_text = _("Choose another vendor")
    register_widget = False
    chosen_view_class = VendorChosenView
    per_page = 50

    def get_object_list(self) -> QuerySet[Vendor]:
        return storefront_vendors().order_by("display_name")


term_chooser = TermChooserViewSet("cms_term_chooser", url_prefix="cms/term-chooser")
category_chooser = CategoryChooserViewSet("cms_category_chooser", url_prefix="cms/category-chooser")
brand_chooser = BrandChooserViewSet("cms_brand_chooser", url_prefix="cms/brand-chooser")
product_chooser = ProductChooserViewSet("cms_product_chooser", url_prefix="cms/product-chooser")
vendor_chooser = VendorChooserViewSet("cms_vendor_chooser", url_prefix="cms/vendor-chooser")

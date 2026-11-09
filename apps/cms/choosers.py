"""Admin choosers for catalog terms, categories, brands, products and vendors (CMS.2 part 2/2,
CMS.10, CMS.3, spec 10 §10.2).

Wagtail's `ChooserViewSet` gives each model a chooser modal under `/admin/` and a StreamField
block (`apps.cms.blocks.TermChooserBlock`, `VendorChooserBlock` and so on). Editors never type a
URL.

- Terms are platform data with no vendor scope: every term is offered, labelled
  "taxonomy code: name" like the staff admin. Categories and brands (the product grid, CMS.10)
  are platform data too: all of them are offered, labelled as in the staff admin.
- Products (the blog's product cards, CMS.3): only listed products, published and of an active
  vendor (`apps.catalog.services.listed_products`), in the list and in the "chosen" step alike,
  labelled by SKU.
- Vendors: only those with a public storefront (`apps.vendors.services.storefront_vendors`), in
  the list and in the "chosen" step alike, so a crafted request cannot pick another vendor.

The viewsets are registered in `wagtail_hooks`. Their widgets are not registered as the default
form widget of foreign keys to these models (`register_widget = False`): the staff admin and other
Wagtail forms keep theirs.
"""

from typing import Any

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from wagtail.admin.views.generic.chooser import ChosenView
from wagtail.admin.viewsets.chooser import ChooserViewSet

from apps.catalog.models import Brand, Category, Product, Term
from apps.catalog.services import listed_products
from apps.vendors.models import Vendor
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
        return Brand.objects.order_by("name")


class ProductChosenView(ChosenView):  # type: ignore[misc]
    def get_object(self, pk: Any) -> Product:
        return listed_products().get(pk=pk)


class ProductChooserViewSet(ChooserViewSet):  # type: ignore[misc]
    model = Product
    icon = "pick"
    choose_one_text = _("Choose a product")
    choose_another_text = _("Choose another product")
    register_widget = False
    chosen_view_class = ProductChosenView
    per_page = 50

    def get_object_list(self) -> QuerySet[Product]:
        return listed_products().order_by("sku")


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

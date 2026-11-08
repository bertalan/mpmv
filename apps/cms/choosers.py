"""Admin choosers for catalog terms and vendors (CMS.2 part 2/2, spec 10 §10.2).

Wagtail's `ChooserViewSet` gives each model a chooser modal under `/admin/` and a StreamField
block (`apps.cms.blocks.TermChooserBlock`, `VendorChooserBlock`). Editors never type a URL.

- Terms are platform data with no vendor scope: every term is offered, labelled
  "taxonomy code: name" like the staff admin.
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

from apps.catalog.models import Term
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
vendor_chooser = VendorChooserViewSet("cms_vendor_chooser", url_prefix="cms/vendor-chooser")

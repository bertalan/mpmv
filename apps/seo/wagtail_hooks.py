"""The SEO panel in the Wagtail admin's Settings menu (SEO.8 2/2), shown to platform staff."""

from decimal import Decimal

from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, include, path, reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from apps.catalog.product_pages import CONSUMER_PRICE_HOOK
from apps.tax.prices import PriceUnavailable, consumer_price
from apps.vendors.models import Vendor

from . import admin_views
from .services import may_edit_seo_settings


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def seo_admin_urls() -> list[URLPattern | URLResolver]:
    patterns = [path("settings/", admin_views.seo_settings, name="settings")]
    return [path("seo/", include((patterns, "seo_admin"), namespace="seo_admin"))]


class _StaffMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return may_edit_seo_settings(request.user)


@hooks.register("register_settings_menu_item")  # type: ignore[untyped-decorator]
def seo_settings_menu_item() -> MenuItem:
    url = reverse_lazy("seo_admin:settings")
    return _StaffMenuItem(_("SEO"), url, icon_name="search", order=500)


def consumer_amount(
    net: Decimal, fiscal_category: str, vendor: Vendor | None, request: HttpRequest | None
) -> Decimal | None:
    """The VAT-inclusive price of a card (TAX.12) for the catalog, which may not import the tax
    app: it orders the other sellers of a product by it (CAT.15). A request-less call gets a
    scratch request so that the rates are read once, not once per card."""
    if vendor is None:
        return None
    try:
        return consumer_price(
            net, fiscal_category, vendor=vendor, request=request or HttpRequest()
        ).gross
    except PriceUnavailable:
        return None


hooks.register(CONSUMER_PRICE_HOOK, consumer_amount)

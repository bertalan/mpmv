"""The SEO panel in the Wagtail admin's Settings menu (SEO.8 2/2), shown to platform staff."""

from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, include, path, reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

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

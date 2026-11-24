"""The palette builder page in the Wagtail admin Settings menu (THM.34)."""

from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, include, path, reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from . import admin_views


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def themes_admin_urls() -> list[URLPattern | URLResolver]:
    patterns = [
        path("palette/", admin_views.palette_builder, name="palette_builder"),
        path("palette/check/", admin_views.palette_check, name="palette_check"),
        path("palette/preview.css", admin_views.palette_preview, name="palette_preview"),
    ]
    return [path("themes/", include((patterns, "themes_admin"), namespace="themes_admin"))]


class _PermissionMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return admin_views.may_use_palette_builder(request.user)


@hooks.register("register_settings_menu_item")  # type: ignore[untyped-decorator]
def palette_builder_menu_item() -> MenuItem:
    url = reverse_lazy("themes_admin:palette_builder")
    return _PermissionMenuItem(_("Palette builder"), url, icon_name="palette", order=510)

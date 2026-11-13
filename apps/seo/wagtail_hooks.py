"""Wagtail hooks of the SEO app: SEO settings panel (SEO.8 2/2), page cache mark (SEO.6)."""

from typing import Any

from django.http import HttpRequest
from django.urls import URLPattern, URLResolver, include, path, reverse_lazy
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.models import Page

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


@hooks.register("before_serve_page", order=100)  # type: ignore[untyped-decorator]
def mark_unrestricted_page(
    page: Page, request: HttpRequest, serve_args: Any, serve_kwargs: Any
) -> None:
    """Tell the page cache (SEO.6) this page has no view restriction on it or its ancestors.

    The hook reads `get_view_restrictions()` itself and does not rely on Wagtail's restriction
    check, which is the `on_serve_page` wrapper that runs after every `before_serve_page` hook
    (wagtail/views.py). A restricted page is therefore marked `False`, and the middleware stores
    only pages marked `True`; a request this hook never reached stays unmarked and is not stored.
    """
    request._page_cache_unrestricted = not page.get_view_restrictions().exists()  # type: ignore[attr-defined]

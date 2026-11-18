"""Wagtail admin hooks of the catalog app (CAT.25 orphan images, CAT.24 rendition monitor)."""

from django.http import HttpRequest
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from .media_cleanup_admin import OrphanImagesView, can_review_orphans
from .rendition_admin import RenditionMonitorView, can_monitor_renditions


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def orphan_image_urls() -> list[URLPattern]:
    return [
        path("images/unused/", OrphanImagesView.as_view(), name="catalog_orphan_images"),
        path(
            "images/renditions/", RenditionMonitorView.as_view(), name="catalog_rendition_monitor"
        ),
    ]


class OrphanImagesMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return can_review_orphans(request)


@hooks.register("register_admin_menu_item")  # type: ignore[untyped-decorator]
def orphan_images_menu_item() -> MenuItem:
    return OrphanImagesMenuItem(
        _("Unused images"), reverse("catalog_orphan_images"), icon_name="bin", order=311
    )


class RenditionMonitorMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return can_monitor_renditions(request)


@hooks.register("register_admin_menu_item")  # type: ignore[untyped-decorator]
def rendition_monitor_menu_item() -> MenuItem:
    return RenditionMonitorMenuItem(
        _("Image renditions"), reverse("catalog_rendition_monitor"), icon_name="image", order=312
    )

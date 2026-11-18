"""Wagtail admin hooks of the catalog app (CAT.25: the orphan image review list)."""

from django.http import HttpRequest
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from .media_cleanup_admin import OrphanImagesView, can_review_orphans


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def orphan_image_urls() -> list[URLPattern]:
    return [
        path("images/unused/", OrphanImagesView.as_view(), name="catalog_orphan_images"),
    ]


class OrphanImagesMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return can_review_orphans(request)


@hooks.register("register_admin_menu_item")  # type: ignore[untyped-decorator]
def orphan_images_menu_item() -> MenuItem:
    return OrphanImagesMenuItem(
        _("Unused images"), reverse("catalog_orphan_images"), icon_name="bin", order=311
    )

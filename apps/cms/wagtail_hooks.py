"""Wagtail admin registrations (CMS.1): navigation menus as snippets, no user management.

Users and groups are managed in the staff admin (`/django-admin/`), which audits every change
(SEC.4); CORE.8 builds the staff menus. Wagtail's own user and group views and its bulk actions
on users would change them without an audit entry, so their registrations are withdrawn here:
they get no URL, no menu item and no admin link.

The term and vendor choosers of the page cards (CMS.2) and the category and brand choosers of
the product grid (CMS.10) and the product chooser of the blog cards (CMS.3) are admin viewsets
(`choosers`). Blog authors (CMS.3) are snippets.

Documents are installed only for wagtail-localize (I18N.3): their menu item is hidden.

Images (CMS.8): the chooser offers only images with their licence and author; the others are
listed under "Images to classify" until staff classify them.
"""

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet
from wagtail.users import wagtail_hooks as wagtail_users_hooks

from apps.core.services import classified

from .admin_views import can_classify_images, image_review
from .choosers import (
    brand_chooser,
    category_chooser,
    product_chooser,
    term_chooser,
    vendor_chooser,
)
from .models import BlogAuthor, NavigationMenu


class NavigationMenuViewSet(SnippetViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = NavigationMenu
    icon = "list-ul"
    menu_label = _("Navigation")
    add_to_admin_menu = True
    list_display = ("location", "locale")
    list_filter = ("location", "locale")


register_snippet(NavigationMenuViewSet)


class BlogAuthorViewSet(SnippetViewSet):  # type: ignore[misc]
    """The blog's authors (CMS.3): a byline, not a user account."""

    model = BlogAuthor
    icon = "user"
    menu_label = _("Blog authors")
    add_to_admin_menu = True
    list_display = ("name",)
    search_fields = ("name",)


register_snippet(BlogAuthorViewSet)


def _withdraw(hook_name: str, registered_by: Callable[[Any], bool]) -> None:
    # Wagtail has no public way to unregister a hook; its registry is a dict of lists.
    registry: dict[str, list[tuple[Any, int]]] = hooks._hooks
    registry[hook_name] = [
        (fn, order) for fn, order in registry.get(hook_name, []) if not registered_by(fn)
    ]


def _from_wagtail_users(fn: Any) -> bool:
    return str(getattr(fn, "__module__", "")).startswith("wagtail.users.")


# Importing wagtail_users_hooks above has registered them, whatever the app order.
_withdraw("register_admin_viewset", lambda fn: fn is wagtail_users_hooks.register_viewset)
_withdraw("register_bulk_action", _from_wagtail_users)


@hooks.register("construct_main_menu")  # type: ignore[untyped-decorator]
def hide_documents_menu(request: Any, menu_items: list[Any]) -> None:
    menu_items[:] = [item for item in menu_items if item.name != "documents"]


@hooks.register("construct_image_chooser_queryset")  # type: ignore[untyped-decorator]
def only_classified_images(images: Any, request: Any) -> Any:
    """Images migrated without licence or author cannot be chosen for new content (CMS.8)."""
    return classified(images)


@hooks.register("register_admin_urls")  # type: ignore[untyped-decorator]
def image_review_urls() -> list[URLPattern]:
    return [path("images/review/", image_review, name="cms_image_review")]


class ImageReviewMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    def is_shown(self, request: HttpRequest) -> bool:
        return can_classify_images(request)


@hooks.register("register_admin_menu_item")  # type: ignore[untyped-decorator]
def image_review_menu_item() -> MenuItem:
    return ImageReviewMenuItem(
        _("Images to classify"), reverse("cms_image_review"), icon_name="image", order=310
    )


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_term_chooser() -> Any:
    return term_chooser


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_vendor_chooser() -> Any:
    return vendor_chooser


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_category_chooser() -> Any:
    return category_chooser


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_brand_chooser() -> Any:
    return brand_chooser


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_product_chooser() -> Any:
    return product_chooser

"""Wagtail admin registrations (CMS.1): navigation menus as snippets, no user management.

Users and groups are managed in the staff admin (`/django-admin/`), which audits every change
(SEC.4); CORE.8 builds the staff menus. Wagtail's own user and group views and its bulk actions
on users would change them without an audit entry, so their registrations are withdrawn here:
they get no URL, no menu item and no admin link.

The term and vendor choosers of the page cards (CMS.2) and the category and brand choosers of
the product grid (CMS.10) and the product chooser of the blog cards (CMS.3) are admin viewsets
(`choosers`). Blog authors (CMS.3) are translatable snippets.

Documents are installed only for wagtail-localize (I18N.3): their menu item is hidden.

Images (CMS.8): the chooser offers only images with their licence and author; the others are
listed under "Images to classify" until staff classify them.

Landing pages (CMS.4): the catalog's listing pages ask the `catalog_listing_editorial` hook for
editorial content, which this app gives from its term and vendor landing pages.

Contextual help (ARC.32): "Help for this page" is an item of the sidebar's Help menu (Wagtail 8.1
has no header and no hook for its account menu); the static script and style come in through
`insert_global_admin_js` and `insert_global_admin_css`, so nothing is inline (`help_admin`).
"""

from collections.abc import Callable
from typing import Any

from django.http import HttpRequest
from django.templatetags.static import static
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.ui.sidebar import LinkMenuItem as LinkMenuItemComponent
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet
from wagtail.users import wagtail_hooks as wagtail_users_hooks

from apps.catalog.listings import EDITORIAL_HOOK, ListingEditorial
from apps.catalog.services import ListingScope
from apps.core.services import classified

from . import help_admin, roles
from .admin_views import can_classify_images, image_review
from .choosers import (
    brand_chooser,
    category_chooser,
    product_chooser,
    term_chooser,
    vendor_chooser,
)
from .models import BlogAuthor, NavigationMenu
from .services import listing_editorial


class NavigationMenuViewSet(SnippetViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = NavigationMenu
    icon = "list-ul"
    menu_label = _("Navigation")
    add_to_admin_menu = True
    list_display = ("location", "locale")
    list_filter = ("location", "locale")


register_snippet(NavigationMenuViewSet)


class BlogAuthorViewSet(SnippetViewSet):  # type: ignore[misc]
    """The blog's authors (CMS.3): a byline with bio and photo, not a user account."""

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
def cms_only_menu(request: Any, menu_items: list[Any]) -> None:
    """The Wagtail admin menu holds CMS entries only (CORE.8): pages, images, snippets, reports,
    settings, help and the event review. Vendors, products, orders, rates and payouts are in the
    Manager (CORE.17), so whatever an app or package adds that is not in the allow list is
    dropped, and so are documents (installed for wagtail-localize only)."""
    menu_items[:] = [item for item in menu_items if item.name in roles.ADMIN_MENU_ALLOWED]


class HelpForThisPageMenuItem(MenuItem):  # type: ignore[misc]  # Wagtail is untyped
    """ "Help for this page" in the sidebar's Help menu (ARC.32). Menu items are built once, so the
    screen's route name, page type and language are put on a component made for each request."""

    def render_component(self, request: HttpRequest) -> Any:
        return LinkMenuItemComponent(
            self.name,
            self.label,
            self.url,
            icon_name=self.icon_name,
            classname=self.classname,
            attrs={**self.attrs, **help_admin.menu_attrs(request)},
        )


@hooks.register("register_help_menu_item")  # type: ignore[untyped-decorator]
def help_for_this_page_menu_item() -> MenuItem:
    return HelpForThisPageMenuItem(
        _("Help for this page"),
        url="#",
        name="help-for-this-page",
        icon_name="help",
        order=900,
    )


@hooks.register("insert_global_admin_js")  # type: ignore[untyped-decorator]
def help_script() -> str:
    return format_html('<script src="{}" defer></script>', static("cms/js/admin-help.js"))


@hooks.register("insert_global_admin_css")  # type: ignore[untyped-decorator]
def help_style() -> str:
    return format_html('<link rel="stylesheet" href="{}">', static("cms/css/admin-help.css"))


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
def register_product_chooser() -> Any:
    return product_chooser


@hooks.register("register_admin_viewset")  # type: ignore[untyped-decorator]
def register_brand_chooser() -> Any:
    return brand_chooser


@hooks.register(EDITORIAL_HOOK)  # type: ignore[untyped-decorator]
def landing_page_editorial(
    request: HttpRequest, scope: ListingScope, language: str
) -> ListingEditorial | None:
    """A term landing page on its term's listing page; the vendor story link on a storefront
    (CMS.4)."""
    return listing_editorial(request, scope, language)

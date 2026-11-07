"""Wagtail admin registrations (CMS.1): navigation menus as snippets, no user management.

Users and groups are managed in the staff admin (`/django-admin/`), which audits every change
(SEC.4); CORE.8 builds the staff menus. Wagtail's own user and group views and its bulk actions
on users would change them without an audit entry, so their registrations are withdrawn here:
they get no URL, no menu item and no admin link.
"""

from collections.abc import Callable
from typing import Any

from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet
from wagtail.users import wagtail_hooks as wagtail_users_hooks

from .models import NavigationMenu


class NavigationMenuViewSet(SnippetViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = NavigationMenu
    icon = "list-ul"
    menu_label = _("Navigation")
    add_to_admin_menu = True
    list_display = ("location", "locale")
    list_filter = ("location", "locale")


register_snippet(NavigationMenuViewSet)


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

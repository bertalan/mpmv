"""Who edits the help texts in the editors' admin (ARC.32, CORE.18).

The `HelpEntry` snippet (apps.cms) is platform content, so its permissions are not Django model
permissions a group could be handed: they come from the role-permission matrix. The row is
"Platform: moderation (vendor events, storefront changes, content)": the Catalog and moderation
role edits, the platform owner reads, no other role has access, and the technical superuser
holds none without a role (break-glass). The check is role membership from `roles.py`, never
`has_perm`. Reaching the editors' admin at all still needs its own access permission.
"""

from collections.abc import Collection
from typing import Any

from django.utils.translation import gettext_lazy as _
from wagtail.permission_policies.base import BasePermissionPolicy
from wagtail.permissions import register_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from apps.accounts.models import User
from apps.cms.models import HelpEntry

from . import roles
from .role_groups import user_roles

HELP_SECTION = "platform.moderation"
READ_ACTIONS = frozenset({"view"})


class HelpEntryPermissionPolicy(BasePermissionPolicy):  # type: ignore[misc]  # Wagtail is untyped
    """Permissions on help entries from the matrix row of `HELP_SECTION`."""

    def user_has_permission(self, user: Any, action: str) -> bool:
        return roles.allows(HELP_SECTION, user_roles(user), write=action not in READ_ACTIONS)

    def user_has_any_permission(self, user: Any, actions: Collection[str]) -> bool:
        return any(self.user_has_permission(user, action) for action in actions)

    def users_with_any_permission(self, actions: Collection[str]) -> Any:
        candidates = User.objects.filter(is_active=True, groups__name__in=roles.group_names())
        allowed = [
            user.pk for user in candidates.distinct() if self.user_has_any_permission(user, actions)
        ]
        return User.objects.filter(pk__in=allowed)


register_permission_policy(HelpEntry, HelpEntryPermissionPolicy(HelpEntry))


class HelpEntryViewSet(SnippetViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = HelpEntry
    icon = "help"
    menu_label = _("Help texts")
    add_to_admin_menu = True
    list_display = ("title", "kind", "key", "locale")
    list_filter = ("kind", "locale")
    search_fields = ("title", "key")


register_snippet(HelpEntryViewSet)

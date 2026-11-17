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

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from wagtail.permission_policies.base import BasePermissionPolicy
from wagtail.permissions import register_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import CopyView, DeleteView, EditView, SnippetViewSet

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

    def user_has_permission_for_instance(self, user: Any, action: str, instance: Any) -> bool:
        """A row that comes from a repository file (ARC.36) is read-only for everyone, the
        superuser included: the file wins and the next sync would overwrite an edit. Edit, delete,
        copy and bulk delete all ask this check. Rows staff made follow the matrix."""
        if action not in READ_ACTIONS and getattr(instance, "source", "") == "file":
            return False
        return self.user_has_permission(user, action)

    def user_has_any_permission(self, user: Any, actions: Collection[str]) -> bool:
        return any(self.user_has_permission(user, action) for action in actions)

    def users_with_any_permission(self, actions: Collection[str]) -> Any:
        candidates = User.objects.filter(is_active=True, groups__name__in=roles.group_names())
        allowed = [
            user.pk for user in candidates.distinct() if self.user_has_any_permission(user, actions)
        ]
        return User.objects.filter(pk__in=allowed)


register_permission_policy(HelpEntry, HelpEntryPermissionPolicy(HelpEntry))


class _FileRowGuard:
    """The generic snippet views check the class-level permission only, so the edit, delete and
    copy views refuse a file-backed row here (403, GET and POST alike). Bulk delete asks the
    instance check of the policy."""

    def get_object(self, queryset: Any = None) -> Any:
        obj = super().get_object(queryset)  # type: ignore[misc]
        if getattr(obj, "source", "") == "file":
            raise PermissionDenied("This help text comes from a repository file: edit the file.")
        return obj


class HelpEntryEditView(_FileRowGuard, EditView):  # type: ignore[misc]  # Wagtail is untyped
    pass


class HelpEntryDeleteView(_FileRowGuard, DeleteView):  # type: ignore[misc]
    pass


class HelpEntryCopyView(_FileRowGuard, CopyView):  # type: ignore[misc]
    pass


class HelpEntryViewSet(SnippetViewSet):  # type: ignore[misc]  # Wagtail is untyped
    model = HelpEntry
    edit_view_class = HelpEntryEditView
    delete_view_class = HelpEntryDeleteView
    copy_view_class = HelpEntryCopyView
    icon = "help"
    menu_label = _("Help texts")
    add_to_admin_menu = True
    list_display = ("title", "kind", "key", "locale", "source")
    list_filter = ("kind", "locale")
    search_fields = ("title", "key")


register_snippet(HelpEntryViewSet)

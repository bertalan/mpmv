"""Editor roles in the database (CORE.8, Q-58): groups and grants made from `roles.ROLES`.

`sync_editor_roles` runs after every `migrate` (and so after every test database flush) and
only adds what is missing: groups, their model permissions, the image and translation
permissions, and one page grant per tree node the role is bound to. `grant_for_new_page` does
the same for one page when it is created, so a legal page or a blog index added later (a new
language, the seed, an editor) is covered at once. Nothing is ever removed, so a platform
owner who narrows a role in the staff admin keeps the change.
"""

from typing import Any

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.db import DEFAULT_DB_ALIAS, connections
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission, Page

from . import roles

IMAGE_PERMISSIONS = ("wagtailimages.add_image", "wagtailimages.change_image")
IMAGE_COLLECTION_PERMISSIONS = (*IMAGE_PERMISSIONS, "wagtailimages.choose_image")
ADMIN_PERMISSIONS = ("wagtailadmin.access_admin",)
TRANSLATION_PERMISSIONS = ("wagtail_localize.submit_translation",)


def _permission(label: str, *, using: str = DEFAULT_DB_ALIAS) -> Permission | None:
    app_label, codename = label.split(".")
    return (
        Permission.objects.using(using)
        .filter(content_type__app_label=app_label, codename=codename)
        .first()
    )


def _page_permission(codename: str, *, using: str = DEFAULT_DB_ALIAS) -> Permission | None:
    return _permission(f"wagtailcore.{codename}", using=using)


def _role(name: str) -> roles.EditorRole:
    return next(role for role in roles.ROLES if role.name == name)


def _ensure_group(role: roles.EditorRole, *, using: str) -> Group:
    group, _ = Group.objects.using(using).get_or_create(name=role.name)
    labels = [*ADMIN_PERMISSIONS, *role.model_permissions]
    if role.images:
        labels += [*TRANSLATION_PERMISSIONS]
    wanted = [p for p in (_permission(label, using=using) for label in labels) if p]
    group.permissions.add(*wanted)
    if role.images:
        root = Collection.objects.using(using).filter(depth=1).first()
        if root is not None:
            for label in IMAGE_COLLECTION_PERMISSIONS:
                permission = _permission(label, using=using)
                if permission is not None:
                    GroupCollectionPermission.objects.using(using).get_or_create(
                        group=group, collection=root, permission=permission
                    )
    return group


def _grant(group: Group, role: roles.EditorRole, page_ids: list[int], *, using: str) -> None:
    permissions = [
        p for p in (_page_permission(c, using=using) for c in role.page_permissions) if p
    ]
    existing = set(
        GroupPagePermission.objects.using(using)
        .filter(group=group, page_id__in=page_ids)
        .values_list("page_id", "permission_id")
    )
    GroupPagePermission.objects.using(using).bulk_create(
        [
            GroupPagePermission(group=group, page_id=page_id, permission=permission)
            for page_id in page_ids
            for permission in permissions
            if (page_id, permission.pk) not in existing
        ]
    )


def grant_role_on_page(role_name: str, page: Page, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Give the role its page permissions on `page` (idempotent; nothing is removed)."""
    role = _role(role_name)
    _grant(_ensure_group(role, using=using), role, [page.pk], using=using)


def grant_for_new_page(page: Page, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Cover a page created after the sync: every role bound to its model gets its grants."""
    label = page._meta.label
    for role in roles.ROLES:
        if label in role.page_models:
            grant_role_on_page(role.name, page, using=using)


def sync_editor_roles(using: str = DEFAULT_DB_ALIAS) -> None:
    """Create the role groups and give each its permissions and page grants (idempotent)."""
    tables = set(connections[using].introspection.table_names())
    needed = {Group._meta.db_table, GroupPagePermission._meta.db_table, Page._meta.db_table}
    if not needed <= tables:
        return  # a partial migrate (an older target): nothing to grant on yet
    for role in roles.ROLES:
        group = _ensure_group(role, using=using)
        page_ids: list[int] = []
        for label in role.page_models:
            model: Any = apps.get_model(label)
            if model._meta.db_table in tables:
                page_ids += list(model._base_manager.using(using).values_list("pk", flat=True))
        if page_ids:
            _grant(group, role, page_ids, using=using)

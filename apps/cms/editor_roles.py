"""Editor roles in the database (CORE.8, Q-58): groups and grants made from `roles.ROLES`.

`sync_editor_roles` runs after every `migrate` but seeds a role only once: the first time it
sees a role without an `EditorRoleSeed` row it creates the group, its permissions, the image
and translation permissions and one page grant per tree node the role is bound to, then writes
the marker. Later runs skip a seeded role, so a platform owner who narrows a role in the staff
admin (removes a permission or a page grant) keeps the change across deploys. Deleting the
marker row seeds the role again.

`grant_for_new_page` gives one new page its grants when it is created (a new language, the
seed, an editor), for roles that exist; it never recreates a group or its permissions.
"""

from typing import Any

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.db import DEFAULT_DB_ALIAS, connections
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission, Page

from . import roles
from .models import EditorRoleSeed

IMAGE_COLLECTION_PERMISSIONS = (
    "wagtailimages.add_image",
    "wagtailimages.change_image",
    "wagtailimages.choose_image",
)
ADMIN_PERMISSIONS = ("wagtailadmin.access_admin",)
TRANSLATION_PERMISSIONS = ("wagtail_localize.submit_translation",)


def _permission(label: str, *, using: str = DEFAULT_DB_ALIAS) -> Permission | None:
    app_label, codename = label.split(".")
    return (
        Permission.objects.using(using)
        .filter(content_type__app_label=app_label, codename=codename)
        .first()
    )


def _role(name: str) -> roles.EditorRole:
    return next(role for role in roles.ROLES if role.name == name)


def _seed_group(role: roles.EditorRole, *, using: str) -> Group:
    group, _ = Group.objects.using(using).get_or_create(name=role.name)
    labels = [*ADMIN_PERMISSIONS, *role.model_permissions]
    if role.images:
        labels += TRANSLATION_PERMISSIONS
    group.permissions.add(*[p for p in (_permission(label, using=using) for label in labels) if p])
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
        p
        for p in (_permission(f"wagtailcore.{c}", using=using) for c in role.page_permissions)
        if p
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
    """Give an existing role its page permissions on `page` (idempotent). A role whose group
    is missing is left to the seed: nothing is created here."""
    role = _role(role_name)
    group = Group.objects.using(using).filter(name=role.name).first()
    if group is not None:
        _grant(group, role, [page.pk], using=using)


def grant_for_new_page(page: Page, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Cover a page created after the seed: every role bound to its model gets its grants."""
    label = page._meta.label
    for role in roles.ROLES:
        if label in role.page_models:
            grant_role_on_page(role.name, page, using=using)


def sync_editor_roles(using: str = DEFAULT_DB_ALIAS) -> None:
    """Seed every role that has not been seeded yet; leave seeded roles as the owner shaped them."""
    tables = set(connections[using].introspection.table_names())
    needed = {
        Group._meta.db_table,
        GroupPagePermission._meta.db_table,
        Page._meta.db_table,
        EditorRoleSeed._meta.db_table,
    }
    if not needed <= tables:
        return  # a partial migrate (an older target): nothing to grant on yet
    seeded = set(EditorRoleSeed.objects.using(using).values_list("role", flat=True))
    for role in roles.ROLES:
        if role.name in seeded:
            continue
        group = _seed_group(role, using=using)
        page_ids: list[int] = []
        for label in role.page_models:
            model: Any = apps.get_model(label)
            if model._meta.db_table in tables:
                page_ids += list(model._base_manager.using(using).values_list("pk", flat=True))
        if page_ids:
            _grant(group, role, page_ids, using=using)
        EditorRoleSeed.objects.using(using).create(role=role.name)

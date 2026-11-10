"""CORE.8 (Q-58): remove Wagtail's default "Editors" and "Moderators" groups.

Our own editor roles replace them (`apps.cms.roles`). Their permissions and page grants are made
after the migrate by `apps.cms.editor_roles.sync_editor_roles`; this step only moves people, so
nobody loses Wagtail access silently:

- members of "Editors" join the three content roles (Page editors, Blog editors, Legal page
  editors); members of "Moderators" join those and the Event moderators. The roles are narrower
  than the defaults, which could edit the whole tree (events included for Editors): a platform
  owner who wants a user back on a part of the tree adds the right role in the staff admin.
- the default "Moderators approval" workflow task keeps working: the content roles are added to
  its groups before the group goes.
- a database without the default groups (STAGE, a fresh install after a flush) is untouched,
  and running the step twice changes nothing.

Reverse: the default groups come back with Wagtail's initial permissions (admin access, image
choose/add/change, page add/change/lock/unlock on the tree root, and publish for Moderators)
and the members of the three content roles return to "Editors". Moderators members are not
restored: the groups cannot tell them apart. Names are written out here on purpose: a migration
does not follow later edits of `roles.py`.
"""

from typing import Any

from django.db import migrations

EDITORS = "Editors"
MODERATORS = "Moderators"
CONTENT_ROLES = ("Page editors", "Blog editors", "Legal page editors")
EVENT_MODERATORS = "Event moderators"
ROLES = (*CONTENT_ROLES, EVENT_MODERATORS)
SUCCESSORS = {EDITORS: CONTENT_ROLES, MODERATORS: (*CONTENT_ROLES, EVENT_MODERATORS)}

PAGE_PERMISSIONS = {
    EDITORS: ("add_page", "change_page", "bulk_delete_page", "lock_page", "unlock_page"),
    MODERATORS: (
        "add_page",
        "change_page",
        "publish_page",
        "bulk_delete_page",
        "lock_page",
        "unlock_page",
    ),
}
IMAGE_PERMISSIONS = ("add_image", "change_image", "choose_image")


def remove_default_groups(apps: Any, schema_editor: Any) -> None:
    Group = apps.get_model("auth", "Group")
    for default, successors in SUCCESSORS.items():
        group = Group.objects.filter(name=default).first()
        if group is None:
            continue
        users = list(group.user_set.all())
        roles = [Group.objects.get_or_create(name=name)[0] for name in successors]
        for role in roles:
            role.user_set.add(*users)
        if default == MODERATORS:
            GroupApprovalTask = apps.get_model("wagtailcore", "GroupApprovalTask")
            content = [role for role in roles if role.name in CONTENT_ROLES]
            for task in GroupApprovalTask.objects.filter(groups=group):
                task.groups.add(*content)
        group.delete()
    for name in ROLES:
        Group.objects.get_or_create(name=name)


def restore_default_groups(apps: Any, schema_editor: Any) -> None:
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    Page = apps.get_model("wagtailcore", "Page")
    GroupPagePermission = apps.get_model("wagtailcore", "GroupPagePermission")
    Collection = apps.get_model("wagtailcore", "Collection")
    GroupCollectionPermission = apps.get_model("wagtailcore", "GroupCollectionPermission")

    root = Page.objects.filter(depth=1).first()
    collection = Collection.objects.filter(depth=1).first()
    admin = Permission.objects.filter(
        content_type__app_label="wagtailadmin", codename="access_admin"
    ).first()
    for name in (EDITORS, MODERATORS):
        group, _ = Group.objects.get_or_create(name=name)
        if admin is not None:
            group.permissions.add(admin)
        if root is not None:
            for codename in PAGE_PERMISSIONS[name]:
                permission = Permission.objects.filter(
                    content_type__app_label="wagtailcore", codename=codename
                ).first()
                if permission is not None:
                    GroupPagePermission.objects.get_or_create(
                        group=group, page=root, permission=permission
                    )
        if collection is not None:
            for codename in IMAGE_PERMISSIONS:
                permission = Permission.objects.filter(
                    content_type__app_label="wagtailimages", codename=codename
                ).first()
                if permission is not None:
                    GroupCollectionPermission.objects.get_or_create(
                        group=group, collection=collection, permission=permission
                    )
    editors = Group.objects.get(name=EDITORS)
    for role in Group.objects.filter(name__in=CONTENT_ROLES):
        editors.user_set.add(*role.user_set.all())


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0011_blog"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("wagtailcore", "0098_apitoken"),
        ("accounts", "0005_action_link_issue"),
    ]

    operations = [migrations.RunPython(remove_default_groups, restore_default_groups)]

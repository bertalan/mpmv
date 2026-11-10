"""CORE.8 (Q-58): remove Wagtail's default "Editors" and "Moderators" groups.

Our own editor roles replace them (`apps.cms.roles`). Their permissions and page grants are made
after the migrate by `apps.cms.editor_roles.sync_editor_roles`; this step only moves people, so
nobody loses Wagtail access silently:

- members of "Editors" and of "Moderators" join the three content roles (Page editors, Blog
  editors, Legal page editors). Nobody is added to Event moderators: that is an explicit
  assignment in the staff admin (no implicit grants, lane-a decision on CORE.8). The roles are
  narrower than the defaults, which could edit the whole tree: a platform owner who wants a
  user back on a part of the tree adds the right role in the staff admin.
- the default "Moderators approval" workflow task keeps working: the publisher roles (empty:
  staff assign them) are added to its groups before the group goes. Editors never approve.
- a database without the default groups (STAGE, a fresh install after a flush) is untouched,
  and running the step twice changes nothing.

Reverse: the default groups come back with Wagtail's initial permissions (admin access, image
choose/add/change, page add/change/lock/unlock on the tree root, and publish for Moderators)
and only users who are in all three content roles (what this step produced for the default
groups) return to "Editors"; someone who holds just one content role keeps no wider access.
Moderators members are not restored: the groups cannot tell them apart. Names are written out
here on purpose: a migration
does not follow later edits of `roles.py`.
"""

from typing import Any

from django.db import migrations, models

EDITORS = "Editors"
MODERATORS = "Moderators"
CONTENT_ROLES = ("Page editors", "Blog editors", "Legal page editors")
PUBLISHER_ROLES = ("Page publishers", "Blog publishers", "Legal page publishers")
EVENT_MODERATORS = "Event moderators"
ROLES = (*CONTENT_ROLES, *PUBLISHER_ROLES, EVENT_MODERATORS)
SUCCESSORS = {EDITORS: CONTENT_ROLES, MODERATORS: CONTENT_ROLES}

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
            publishers = [Group.objects.get_or_create(name=name)[0] for name in PUBLISHER_ROLES]
            for task in GroupApprovalTask.objects.filter(groups=group):
                task.groups.add(*publishers)
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
    roles = list(Group.objects.filter(name__in=CONTENT_ROLES))
    if len(roles) == len(CONTENT_ROLES):
        in_all = set.intersection(
            *(set(role.user_set.values_list("pk", flat=True)) for role in roles)
        )
        editors.user_set.add(*in_all)


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0011_blog"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("wagtailcore", "0098_apitoken"),
        ("accounts", "0005_action_link_issue"),
    ]

    operations = [
        migrations.CreateModel(
            name="EditorRoleSeed",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("role", models.CharField(max_length=100, unique=True)),
                ("seeded_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.RunPython(remove_default_groups, restore_default_groups),
    ]

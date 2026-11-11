"""Seed the platform role groups from `roles.py` (CORE.18). Idempotent; nobody is added."""

from django.db import migrations

from apps.manager.role_groups import refuse_adopting_populated_groups, sync_role_groups


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    group = apps.get_model("auth", "Group")
    refuse_adopting_populated_groups(group)
    sync_role_groups(
        group,
        apps.get_model("auth", "Permission"),
        apps.get_model("contenttypes", "ContentType"),
        apps.get_model,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("manager", "0001_platform_access"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0014_platform_owner_allowed_vendor_countries"),
        ("vendors", "0021_platform_staff_permission"),
        # roles.py names Wagtail's admin access permission (ARC.32): its model must exist here.
        ("wagtailadmin", "0001_create_admin_access_permissions"),
        # Wagtail's own migrations give their "choose" permissions to every group that holds the
        # admin access: they must run before the groups exist, or the groups would drift.
        ("wagtailimages", "0023_add_choose_permissions"),
        ("wagtaildocs", "0011_add_choose_permissions"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

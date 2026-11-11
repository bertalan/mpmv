"""Seed the platform role groups from `roles.py` (CORE.18). Idempotent; nobody is added."""

from django.db import migrations

from apps.manager.role_groups import sync_role_groups


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    sync_role_groups(
        apps.get_model("auth", "Group"),
        apps.get_model("auth", "Permission"),
        apps.get_model("contenttypes", "ContentType"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("manager", "0001_platform_access"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0014_platform_owner_allowed_vendor_countries"),
        ("vendors", "0021_platform_staff_permission"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

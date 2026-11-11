"""Give the roles that edit or read the help texts Wagtail's admin access (ARC.32, CORE.18).

Runs the same idempotent sync as the seed: the Catalog and moderation role and the platform
owner role gain `wagtailadmin.access_admin`; membership is never touched. To be renumbered after
the other open manager migrations land.
"""

from django.db import migrations

from apps.manager.role_groups import sync_role_groups


def sync(apps, schema_editor):  # type: ignore[no-untyped-def]
    sync_role_groups(
        apps.get_model("auth", "Group"),
        apps.get_model("auth", "Permission"),
        apps.get_model("contenttypes", "ContentType"),
        apps.get_model,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("manager", "0002_seed_role_groups"),
        ("wagtailadmin", "0001_create_admin_access_permissions"),
    ]
    operations = [migrations.RunPython(sync, migrations.RunPython.noop)]

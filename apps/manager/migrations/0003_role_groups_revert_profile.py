"""Give the catalog and owner role groups the storefront revert permission (CORE.9 3/3, CORE.18).

Re-syncs the role groups from `roles.py`. Idempotent; membership is never touched.
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
        ("vendors", "0027_revert_storefront_profile_permission"),
    ]
    operations = [migrations.RunPython(sync, migrations.RunPython.noop)]

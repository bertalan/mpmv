"""Give the owner, support and finance roles the passkey-window permission (CORE.25, D-32).

Owner decision 2026-11-12: these three roles hold `core.elevate`. Runs the same idempotent sync as
the seed; membership is never touched.
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
        ("manager", "0003_role_groups_admin_access"),
        ("core", "0020_session_policy_elevate"),
    ]
    operations = [migrations.RunPython(sync, migrations.RunPython.noop)]

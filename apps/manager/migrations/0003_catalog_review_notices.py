"""Catalog and moderation holds `compliance.review_notices` (CORE.18, matrix row notices)."""

from django.db import migrations

from apps.manager.role_groups import sync_role_groups


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    sync_role_groups(
        apps.get_model("auth", "Group"),
        apps.get_model("auth", "Permission"),
        apps.get_model("contenttypes", "ContentType"),
        apps.get_model,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("manager", "0002_seed_role_groups"),
        ("compliance", "0005_erasure_request"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

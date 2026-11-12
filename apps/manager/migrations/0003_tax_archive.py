"""The Tax/Audit platform role (MIG.4): its permission, then its group seeded from `roles.py`."""

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
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="platformaccess",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": [
                    ("platform_config", "Platform role: configuration and tax"),
                    ("platform_catalog", "Platform role: catalog and moderation"),
                    ("platform_finance", "Platform role: finance and payments"),
                    ("platform_support", "Platform role: support"),
                    ("platform_tax_audit", "Platform role: tax audit"),
                ],
            },
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]

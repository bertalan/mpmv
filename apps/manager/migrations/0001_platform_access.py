from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PlatformAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
            ],
            options={
                "permissions": [
                    ("platform_config", "Platform role: configuration and tax"),
                    ("platform_catalog", "Platform role: catalog and moderation"),
                    ("platform_finance", "Platform role: finance and payments"),
                    ("platform_support", "Platform role: support"),
                ],
                "managed": False,
                "default_permissions": (),
            },
        ),
    ]

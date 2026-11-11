"""ORD.16: the language of a vendor's order emails (empty: the platform language)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0026_storefront_rich_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendor",
            name="mail_language",
            field=models.CharField(
                blank=True,
                choices=[
                    ("en", "English"),
                    ("it", "Italiano"),
                    ("de", "Deutsch"),
                    ("fr", "Français"),
                    ("es", "Español"),
                ],
                help_text=(
                    "The language of the order emails we send you, for example a new order to "
                    "ship. Leave it empty to receive them in English."
                ),
                max_length=8,
                verbose_name="Email language",
            ),
        ),
    ]

"""ORD.16: the language of a vendor's order emails (empty: the platform language)."""

from django.db import migrations

import apps.core.fields
import apps.core.i18n


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0030_revert_storefront_profile_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendor",
            name="mail_language",
            field=apps.core.fields.LanguageField(
                blank=True,
                choices=apps.core.i18n.language_choices,
                help_text=(
                    "The language of the order emails we send you, for example a new order to "
                    "ship. Leave it empty to receive them in English."
                ),
                max_length=8,
                verbose_name="Email language",
            ),
        ),
    ]

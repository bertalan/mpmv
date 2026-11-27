"""TAX.19: `VatValidation.country` accepts XI, the VIES prefix of Northern Ireland.

Only the field class and its help text change (validation in Python): no database change.
"""

import apps.tax.fields
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0010_field_help_texts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vatvalidation",
            name="country",
            field=apps.tax.fields.ViesCountryField(
                help_text="ISO 3166-1 alpha-2 (GR for a VIES EL number), or XI for Northern Ireland.",
                max_length=2,
            ),
        ),
    ]

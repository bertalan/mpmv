"""Country group codes in lower case (zones reference them so); a vendor's shipping methods and
pickup options protect it from deletion, like its zones (cleanup batch D, SHP.12)."""

from typing import Any

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


def lower_case_codes(apps: Any, schema_editor: Any) -> None:
    """Lower-case the codes stored before the constraint. Two codes equal but for case stop the
    migration on the unique constraint: no zone could reference the capitalised one anyway."""
    country_group = apps.get_model("shipping", "CountryGroup")
    for group in country_group.objects.exclude(code=django.db.models.functions.text.Lower("code")):
        group.code = group.code.lower()
        group.save(update_fields=["code"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0005_seed_country_groups"),
        ("vendors", "0012_vendorfeatureflag"),
    ]

    operations = [
        migrations.AlterField(
            model_name="countrygroup",
            name="code",
            field=models.SlugField(help_text="Lower case, e.g. eu or dach.", unique=True),
        ),
        migrations.AlterField(
            model_name="pickupoption",
            name="vendor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pickup_options",
                to="vendors.vendor",
            ),
        ),
        migrations.AlterField(
            model_name="shippingmethod",
            name="vendor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="shipping_methods",
                to="vendors.vendor",
            ),
        ),
        migrations.RunPython(lower_case_codes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="countrygroup",
            constraint=models.CheckConstraint(
                condition=models.Q(("code", django.db.models.functions.text.Lower("code"))),
                name="shipping_group_code_lower_case",
            ),
        ),
    ]

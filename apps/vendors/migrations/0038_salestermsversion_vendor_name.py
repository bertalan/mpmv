"""Store the shop name on each sales terms version at publish time (VEN.36).

New versions only: the table is append-only at the database level (0034), so a backfill of
older rows would need the trigger lifted. Older rows stay empty and show the current name.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vendors", "0037_return_policy_version_frozen_once_approved")]

    operations = [
        migrations.AddField(
            model_name="salestermsversion",
            name="vendor_name",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "The shop name when this version was published, so an old version keeps the "
                    "name it was published under. Empty on versions published before it was "
                    "recorded."
                ),
                max_length=150,
            ),
        ),
    ]

"""ARC.17: drop PlatformOperator.einvoice_channel (the platform issues no invoices)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_operator_alert_emails"),
    ]

    operations = [
        # A default first, so that reversing the removal can re-add the column on existing rows.
        migrations.AlterField(
            model_name="platformoperator",
            name="einvoice_channel",
            field=models.CharField(
                choices=[
                    ("none", "No structured e-invoicing"),
                    ("sdi", "SDI (FatturaPA)"),
                    ("peppol", "Peppol"),
                ],
                default="none",
                max_length=16,
                verbose_name="e-invoice channel",
            ),
        ),
        migrations.RemoveField(
            model_name="platformoperator",
            name="einvoice_channel",
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0004_refund_reversal_pending"),
    ]

    operations = [
        migrations.AddField(
            model_name="refund",
            name="states_pending",
            field=models.BooleanField(default=False),
        ),
    ]

"""VEN.21: onboarding rounds, so a reopened vendor accepts the contract in force again.

Existing vendors and acceptances get round 1 from the column default: adding a column with a
default updates no row, so the append-only trigger on acceptances (0010) is not involved.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendors', '0014_fiscal_regime_rf20'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='contractacceptance',
            name='vendors_acceptance_vendor_contract_unique',
        ),
        migrations.AddField(
            model_name='contractacceptance',
            name='onboarding_round',
            field=models.PositiveIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name='vendor',
            name='onboarding_round',
            field=models.PositiveIntegerField(default=1, editable=False, help_text='1 for the first onboarding, one more each time staff reopen the closed vendor.'),
        ),
        migrations.AddConstraint(
            model_name='contractacceptance',
            constraint=models.UniqueConstraint(fields=('vendor', 'contract', 'onboarding_round'), name='vendors_acceptance_vendor_contract_round_unique'),
        ),
    ]

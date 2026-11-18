"""ORD.29: a vendor order may keep its shipping split across VAT rates.

Additive and safe on existing rows: the shipping rate and reason become nullable (NULL = split,
the portions in the shipping choice and in v1 hold the rates) and the consistency check accepts a
NULL rate. No row is rewritten: every existing vendor order is single-rate and keeps its check.
"""

import apps.core.fields
import django.db.models.expressions
import django.db.models.functions.math
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0020_ord20_order_events'),
        ('vendors', '0034_salestermsversion_append_only'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='vendororder',
            name='orders_vendororder_shipping_tax_consistent',
        ),
        migrations.AlterField(
            model_name='vendororder',
            name='shipping_tax_amount',
            field=apps.core.fields.MoneyField(decimal_places=2, default=0, help_text='Snapshot: VAT on the shipping (the sum of its portions).', max_digits=12),
        ),
        migrations.AlterField(
            model_name='vendororder',
            name='shipping_tax_rate',
            field=models.DecimalField(blank=True, decimal_places=3, default=0, help_text='Snapshot: VAT rate on the shipping; empty when split across rates (portions).', max_digits=6, null=True),
        ),
        migrations.AlterField(
            model_name='vendororder',
            name='shipping_tax_reason',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name='vendororder',
            constraint=models.CheckConstraint(condition=models.Q(('shipping_tax_rate__isnull', True), ('shipping_tax_amount', django.db.models.functions.math.Round(django.db.models.expressions.CombinedExpression(django.db.models.expressions.CombinedExpression(models.F('shipping_net'), '*', models.F('shipping_tax_rate')), '/', models.Value(100)), 2)), _connector='OR'), name='orders_vendororder_shipping_tax_consistent'),
        ),
    ]

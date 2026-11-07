# Written by hand (SHP.12): VendorZoneRate becomes ShippingMethod in place, so every existing
# rate keeps its row, id, public id and weight bands. The constraints that name the old model or
# field are dropped here and recreated by 0003 under the new names.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(model_name='weightband', name='shipping_band_no_overlap'),
        migrations.RemoveConstraint(model_name='vendorzonerate', name='shipping_rate_vendor_zone'),
        migrations.RemoveConstraint(
            model_name='vendorzonerate', name='shipping_rate_over_max_weight_valid'
        ),
        migrations.RenameModel(old_name='VendorZoneRate', new_name='ShippingMethod'),
        migrations.RenameField(model_name='weightband', old_name='rate', new_name='method'),
    ]

"""TAX.17: shipping VAT always follows the goods (D-03 decided, adviser opinion 2026-11-11).

Every stored method with another treatment (`standard_rate`, `exempt`) is set to
`follows_goods`; the rows it changed are reported (logged) so the owner can tell the vendors.
The schema step that removes the other choices is the next migration.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)

FOLLOWS_GOODS = "follows_goods"


def follow_the_goods(apps, schema_editor):  # type: ignore[no-untyped-def]
    Method = apps.get_model("shipping", "ShippingMethod")
    rows = list(
        Method.objects.exclude(tax_treatment=FOLLOWS_GOODS).values_list(
            "pk", "vendor_id", "zone_id", "key", "tax_treatment"
        )
    )
    for pk, vendor_id, zone_id, key, old in rows:
        logger.warning(
            "TAX.17: shipping method %s (vendor %s, zone %s, key %r) changed from %r to %r",
            pk,
            vendor_id,
            zone_id,
            key,
            old,
            FOLLOWS_GOODS,
        )
    Method.objects.exclude(tax_treatment=FOLLOWS_GOODS).update(tax_treatment=FOLLOWS_GOODS)
    logger.warning("TAX.17: %d shipping method(s) set to follows_goods", len(rows))


class Migration(migrations.Migration):
    dependencies = [("shipping", "0011_field_help_texts")]

    operations = [migrations.RunPython(follow_the_goods, migrations.RunPython.noop)]

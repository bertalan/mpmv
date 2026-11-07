"""SHP.12 data migration: keep the meaning of the SHP.1 configuration.

- Every former vendor-zone rate was the only one of its vendor and zone: it becomes the default
  method (the partial unique constraint holds, since rates were unique per vendor and zone).
- Postcode ranges move from `70000-74999` to `70000..74999`: only two numbers of the same length
  joined by a dash were ranges; anything else (PT `9000-001`, PL `00-950`) stays an exact
  postcode. The reverse turns `..` back into `-`.
"""

import re
from typing import Any

from django.db import migrations

# Frozen copy of the SHP.1 range syntax at the time of this migration.
OLD_RANGE = re.compile(r'(\d+)-(\d+)', re.ASCII)
NEW_RANGE = re.compile(r'(\d+)\.\.(\d+)', re.ASCII)


def _upgrade(pattern: str) -> str:
    match = OLD_RANGE.fullmatch(pattern.strip())
    if match is None or len(match.group(1)) != len(match.group(2)):
        return pattern
    return f'{match.group(1)}..{match.group(2)}'


def _downgrade(pattern: str) -> str:
    match = NEW_RANGE.fullmatch(pattern.strip())
    return pattern if match is None else f'{match.group(1)}-{match.group(2)}'


def _rewrite(apps: Any, convert: Any) -> None:
    ShippingZone = apps.get_model('shipping', 'ShippingZone')
    for zone in ShippingZone.objects.exclude(postcode_patterns=[]):
        patterns = [convert(p) for p in zone.postcode_patterns]
        if patterns != zone.postcode_patterns:
            zone.postcode_patterns = patterns
            zone.save(update_fields=['postcode_patterns'])


def _mark_defaults(apps: Any) -> None:
    """The first method of each vendor and zone without a default becomes the default."""
    ShippingMethod = apps.get_model('shipping', 'ShippingMethod')
    seen: set[tuple[int, int]] = set(
        ShippingMethod.objects.filter(is_default=True).values_list('vendor_id', 'zone_id')
    )
    for method in ShippingMethod.objects.order_by('id'):
        pair = (method.vendor_id, method.zone_id)
        if pair not in seen:
            seen.add(pair)
            method.is_default = True
            method.save(update_fields=['is_default'])


def forwards(apps: Any, schema_editor: Any) -> None:
    _mark_defaults(apps)
    _rewrite(apps, _upgrade)


def backwards(apps: Any, schema_editor: Any) -> None:
    _rewrite(apps, _downgrade)


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0003_shipping_methods_vendor_zones'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

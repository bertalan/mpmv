"""ARC.8: a row for each kill switch, at its environment default, so staff can pull one in an
emergency without creating it first. Later changes go through the admin (audited)."""

from typing import Any

from django.db import migrations

# Frozen copy of the kill switch names at the time of this migration.
KILL_SWITCHES = ('checkout', 'import', 'payments')


def seed(apps: Any, schema_editor: Any) -> None:
    FeatureFlag = apps.get_model('core', 'FeatureFlag')
    for name in KILL_SWITCHES:
        FeatureFlag.objects.get_or_create(name=name, defaults={'state': 'default'})


class Migration(migrations.Migration):
    dependencies = [('core', '0007_featureflag')]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

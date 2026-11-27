"""ORD.41: `business_time_zone_at(ts)`, the database twin of `services.business_tz(at)` (ARC.27).

The business time zone of the operator version valid at `ts` (`valid_from <= ts < valid_to`);
a moment before the first configuration reads the version valid now; with no operator, the
configured zone that every connection carries as `mpmv.business_time_zone` (set from
`MPMV_BUSINESS_TIME_ZONE` by `signals.carry_business_time_zone`), and UTC when that is empty.
The exclusion constraint of migration 0003 keeps at most one version valid at any moment.
STABLE, not IMMUTABLE: it reads a table and a setting. The order triggers call it (orders 0024).
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION business_time_zone_at(ts timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT business_time_zone FROM core_platformoperator
          WHERE valid_from <= ts AND (valid_to IS NULL OR valid_to > ts)),
        (SELECT business_time_zone FROM core_platformoperator
          WHERE valid_from <= now() AND (valid_to IS NULL OR valid_to > now())),
        NULLIF(current_setting('mpmv.business_time_zone', true), ''),
        'UTC')
$$;
"""

REVERSE = "DROP FUNCTION business_time_zone_at(timestamptz);"


class Migration(migrations.Migration):
    dependencies = [("core", "0035_platformoperator_business_time_zone")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]

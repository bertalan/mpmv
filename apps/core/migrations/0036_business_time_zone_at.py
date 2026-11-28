"""ORD.41: `business_time_zone_at(ts)`, the database twin of `services.business_tz(at)` (ARC.27).

The business time zone of the operator version valid at `ts` (`valid_from <= ts < valid_to`);
a moment before the first configuration reads the version valid now; with no operator, the
configured zone that every connection carries as `mpmv.business_time_zone` (set from
`MPMV_BUSINESS_TIME_ZONE` by `signals.carry_business_time_zone`), and UTC when that is empty.
The exclusion constraint of migration 0003 keeps at most one version valid at any moment.
STABLE, not IMMUTABLE: it reads a table and a setting. The order triggers call it (orders 0025).
A fixed `search_path` and a schema-qualified table keep a caller's path from redirecting it.

Limitation (only with no operator row at all): a client that is not this Django project (psql,
pg_restore, a script) has no `mpmv.business_time_zone` and gets UTC; so does a connection behind
a transaction-mode pooler, where a session setting can be lost or shared between clients. There
is no pooler on STAGE or LIVE today; one in front of Django must keep session settings (session
mode) or this fallback reads UTC. Once an operator row exists the setting is never read."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION business_time_zone_at(ts timestamptz) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
    SELECT COALESCE(
        (SELECT business_time_zone FROM public.core_platformoperator
          WHERE valid_from <= ts AND (valid_to IS NULL OR valid_to > ts)),
        (SELECT business_time_zone FROM public.core_platformoperator
          WHERE valid_from <= now() AND (valid_to IS NULL OR valid_to > now())),
        NULLIF(current_setting('mpmv.business_time_zone', true), ''),
        'UTC')
$$;
"""

REVERSE = "DROP FUNCTION business_time_zone_at(timestamptz);"


class Migration(migrations.Migration):
    dependencies = [("core", "0035_platformoperator_business_time_zone")]

    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]

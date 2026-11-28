"""Let the retention purge delete API mutation log rows older than 12 months (CORE.29, Q-14).

The log stays append-only: UPDATE is refused for every row and DELETE for every row younger than
12 months, whatever the client. Only rows past their retention can leave, so the nightly purge
cannot remove recent evidence even by mistake.
"""

from django.db import migrations

# Why the Python cutoff can never be later than the trigger's: the purge computes
# `months_before(now, 12)` first, then deletes; `now()` in the trigger is read later, in the
# DELETE's transaction. So the trigger's cutoff is the same or later, and every row the purge
# selects (older than its own cutoff) is also older than the trigger's. The trigger can refuse
# only a row the purge never asked for.
RETAIN = """
CREATE OR REPLACE FUNCTION api_apimutationlog_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.created_at < now() - interval '12 months' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'api_apimutationlog is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""

STRICT = """
CREATE OR REPLACE FUNCTION api_apimutationlog_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'api_apimutationlog is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("api", "0007_refused_key")]

    operations = [migrations.RunSQL(RETAIN, reverse_sql=STRICT)]

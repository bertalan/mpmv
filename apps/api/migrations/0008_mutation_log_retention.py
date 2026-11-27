"""Let the retention purge delete API mutation log rows older than 12 months (CORE.29, Q-14).

The log stays append-only: UPDATE is refused for every row and DELETE for every row younger than
12 months, whatever the client. Only rows past their retention can leave, so the nightly purge
cannot remove recent evidence even by mistake.
"""

from django.db import migrations

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

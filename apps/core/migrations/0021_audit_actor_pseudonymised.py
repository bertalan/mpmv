"""One-off: pseudonymise the old customer actor labels in the audit log (CMP.4).

Until now `actor_label` of a customer was `user:<email>`. Going forward it is
`user:<public id>` (`apps.core.audit.user_context`). This migration rewrites the old customer
rows the same way; a row whose user no longer exists becomes `user:unknown`. Staff labels
(`staff:<email>`) are kept: the audit identity of staff is needed to establish and defend legal
claims (GDPR article 17(3)(e)) and for the accounting duties of D-29 and D-30 (article 17(3)(b)).

This is the single exception to the append-only rule of `core_auditlog` (docs/dev/audit-trail.md):
the migration disables the append-only trigger for this one UPDATE, inside the migration's
transaction, and enables it again straight after. It writes one audit row with counts only. It
also replaces an address inside the target key of old lockout entries (`login:account:<email>`)
with a keyed hash. One UPDATE per distinct row set is fine: no real audit data exists before
LIVE, so the table is small. A staff member who later lost the role keeps the `staff:` label
(article 17(3)(e) basis above).
"""

from django.db import migrations

from apps.core.privacy import keyed_hash

TRIGGER = "core_auditlog_no_update_delete"


def pseudonymise(apps, schema_editor) -> None:  # type: ignore[no-untyped-def]
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM core_auditlog WHERE actor_label LIKE 'user:%%@%%'")
        total = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM core_auditlog a WHERE a.actor_label LIKE 'user:%%@%%' "
            "AND NOT EXISTS (SELECT 1 FROM accounts_user u WHERE u.id = a.actor_id)"
        )
        unknown = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM core_auditlog WHERE target_key LIKE 'login:account:%%@%%'"
        )
        if not total and not cursor.fetchone()[0]:
            return
        keys = 0
        # The migration runs in one transaction: a failure rolls the DISABLE back too, so the
        # trigger cannot stay off and no try/finally is needed.
        cursor.execute(f"ALTER TABLE core_auditlog DISABLE TRIGGER {TRIGGER}")
        cursor.execute(
            "UPDATE core_auditlog a SET actor_label = 'user:' || COALESCE("
            "(SELECT u.public_id::text FROM accounts_user u WHERE u.id = a.actor_id), "
            "'unknown') WHERE a.actor_label LIKE 'user:%%@%%'"
        )
        cursor.execute(
            "SELECT DISTINCT target_key FROM core_auditlog "
            "WHERE target_key LIKE 'login:account:%%@%%'"
        )
        for (old,) in cursor.fetchall():
            cursor.execute(
                "UPDATE core_auditlog SET target_key = %s WHERE target_key = %s",
                [
                    "login:account:"
                    + keyed_hash(old.removeprefix("login:account:"), purpose="audit-login-account"),
                    old,
                ],
            )
            keys += 1
        cursor.execute(f"ALTER TABLE core_auditlog ENABLE TRIGGER {TRIGGER}")
    AuditLog = apps.get_model("core", "AuditLog")
    AuditLog.objects.create(
        actor_label="system:migration",
        source="system",
        action="core.audit_actor_pseudonymised",
        target_model="core.auditlog",
        target_key="actor_label",
        before=None,
        after={"rewritten": total, "unknown": unknown, "lockout_keys": keys},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_session_policy_elevate"),
        ("accounts", "0001_initial"),
    ]

    operations = [migrations.RunPython(pseudonymise, migrations.RunPython.noop)]

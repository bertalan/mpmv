"""CMP.4 (owner decision, lane-a answer 6784046561): a one-off migration rewrites the old
customer actor labels `user:<email>` to `user:<public id>`; staff labels stay (GDPR art.
17(3)(b) and (e), D-29 and D-30 retention). It is the single exception to append-only."""

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.core.migrations.0021_audit_actor_pseudonymised")


def _log(label: str, actor_id: int | None) -> AuditLog:
    return AuditLog.objects.create(
        actor_id=actor_id,
        actor_label=label,
        source="system",
        action="x.y",
        target_model="m",
        target_key="k",
    )


def _run() -> None:
    migration.pseudonymise(apps, SimpleNamespace(connection=connection))


def test_old_customer_labels_get_the_public_id_and_staff_labels_stay() -> None:
    customer = UserFactory.create(email="mario@example.com")
    staff = UserFactory.create(staff=True, email="boss@example.com")
    c = _log("user:mario@example.com", customer.pk)
    s = _log("staff:boss@example.com", staff.pk)
    ghost = _log("user:gone@example.com", 999999)
    new = _log(f"user:{customer.public_id}", customer.pk)

    _run()

    for row in (c, s, ghost, new):
        row.refresh_from_db()
    assert c.actor_label == f"user:{customer.public_id}"
    assert s.actor_label == "staff:boss@example.com"
    assert ghost.actor_label == "user:unknown"
    assert new.actor_label == f"user:{customer.public_id}"


def test_the_migration_audits_itself_with_counts_only_and_restores_the_trigger() -> None:
    customer = UserFactory.create(email="mario@example.com")
    _log("user:mario@example.com", customer.pk)
    _log("user:gone@example.com", None)
    _run()
    entry = AuditLog.objects.get(action="core.audit_actor_pseudonymised")
    blob = f"{entry.before} {entry.after} {entry.actor_label}"
    assert "@" not in blob
    assert (entry.after or {})["rewritten"] == 2
    assert (entry.after or {})["unknown"] == 1
    with pytest.raises(Exception, match="append-only"):
        AuditLog.objects.filter(pk=entry.pk).update(actor_label="x")


def test_old_lockout_target_keys_with_an_address_become_a_keyed_hash() -> None:
    from apps.core.privacy import keyed_hash

    row = AuditLog.objects.create(
        actor_label="anonymous",
        source="system",
        action="accounts.login.locked_out",
        target_model="core.throttle",
        target_key="login:account:mario@example.com",
    )
    ip_row = AuditLog.objects.create(
        actor_label="anonymous",
        source="system",
        action="accounts.login.locked_out",
        target_model="core.throttle",
        target_key="login:ip:203.0.113.9",
    )
    _run()
    row.refresh_from_db()
    ip_row.refresh_from_db()
    assert row.target_key == "login:account:" + keyed_hash(
        "mario@example.com", purpose="audit-login-account"
    )
    assert "@" not in row.target_key
    assert ip_row.target_key == "login:ip:203.0.113.9"

"""FIT.5 follow-up: migration 0008 lowers a stored retention above 36 months and audits it."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("fitment", "0007_garage_retention_settings")]


def _migrate_to_0007_with(months: int):  # type: ignore[no-untyped-def]
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps.get_model("fitment", "FitmentScheme")
    old.objects.update(garage_inactivity_months=months)
    executor.loader.build_graph()
    return executor, old


@pytest.mark.django_db(transaction=True)
def test_a_stored_120_is_lowered_to_36_with_an_audit_entry() -> None:
    from apps.core.models import AuditLog
    from apps.fitment import services

    executor, _old = _migrate_to_0007_with(120)
    executor.migrate(executor.loader.graph.leaf_nodes())
    scheme = services.current_scheme()
    assert scheme.garage_inactivity_months == 36
    entry = AuditLog.objects.get(action=services.SCHEME_UPDATED, actor_label="system:migration")
    assert entry.before == {"garage_inactivity_months": 120}
    assert entry.after == {"garage_inactivity_months": 36}
    assert entry.target_key == str(scheme.public_id)


@pytest.mark.django_db(transaction=True)
def test_a_value_within_the_cap_is_left_alone_and_not_audited() -> None:
    from apps.core.models import AuditLog
    from apps.fitment import services

    executor, _old = _migrate_to_0007_with(30)
    executor.migrate(executor.loader.graph.leaf_nodes())
    assert services.current_scheme().garage_inactivity_months == 30
    assert not AuditLog.objects.filter(
        actor_label="system:migration", action=services.SCHEME_UPDATED
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_the_migration_reverses() -> None:
    executor, _old = _migrate_to_0007_with(24)
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor.loader.build_graph()
    executor.migrate(BEFORE)  # a no-op reverse
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())

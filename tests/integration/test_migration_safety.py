"""CORE.27: database-backed system checks never touch their tables while their app is unmigrated."""

from collections.abc import Callable
from typing import Any

import pytest
from django.core import checks
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test.utils import CaptureQueriesContext

from apps.cms.checks import check_reserved_root_slugs, check_snippet_workflow
from apps.core import encryption
from apps.core.checks import check_encrypted_data
from apps.core.language_checks import check_language_state
from apps.core.migration_safety import migrations_pending, skip_while_pending
from apps.core.site_checks import check_site_hostname
from apps.manager.checks import check_role_drift
from apps.themes.checks import check_site_themes

pytestmark = pytest.mark.django_db


def _unapply_one(app_label: str) -> None:
    """Forget one migration of the app, as on a database from the previous release."""
    first = MigrationRecorder.Migration.objects.filter(app=app_label).order_by("-name").first()
    assert first is not None
    first.delete()


def _encrypted_apps() -> list[str]:
    return sorted({c.model._meta.app_label for c in encryption.encrypted_columns()})


CHECKS: list[tuple[str, Callable[..., Any], Callable[[], list[str]], tuple[str, ...]]] = [
    ("encrypted", check_encrypted_data, _encrypted_apps, ("encrypted",)),
    ("site_hostname", check_site_hostname, lambda: ["wagtailcore"], ("wagtailcore_site",)),
    ("language_state", check_language_state, lambda: ["core"], ("core_sitelanguages",)),
    ("reserved_slugs", check_reserved_root_slugs, lambda: ["wagtailcore"], ("wagtailcore_page",)),
    ("snippet_workflow", check_snippet_workflow, lambda: ["cms"], ("cms_contentsnippet",)),
    ("site_themes", check_site_themes, lambda: ["themes"], ("themes_themesettings",)),
    ("role_drift", check_role_drift, lambda: ["manager"], ("auth_group", "manager_")),
]


def test_a_fully_migrated_database_has_nothing_pending() -> None:
    assert not migrations_pending("core", "manager", "cms", "themes", "wagtailcore")


def test_an_unrecorded_migration_is_pending_and_other_apps_are_not() -> None:
    _unapply_one("manager")
    assert migrations_pending("manager")
    assert migrations_pending("core", "manager")
    assert not migrations_pending("core")


def test_an_app_without_migrations_is_never_pending() -> None:
    assert not migrations_pending("no_such_app")


@pytest.mark.parametrize(("name", "check", "labels", "tables"), CHECKS, ids=[c[0] for c in CHECKS])
def test_a_check_skips_while_its_app_has_a_pending_migration(
    name: str, check: Callable[..., Any], labels: Callable[[], list[str]], tables: tuple[str, ...]
) -> None:
    for label in labels():
        _unapply_one(label)
    with CaptureQueriesContext(connection) as queries:
        messages = check(None, databases=["default"])
    assert [m for m in messages if m.id.endswith(("E004", "E003"))] == []
    touched = [q["sql"] for q in queries if any(t in q["sql"] for t in tables)]
    assert touched == [], f"{name} read its tables while migrations were pending"


def test_the_decorator_skips_while_pending_and_runs_afterwards() -> None:
    @skip_while_pending("manager")
    def check(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
        return [checks.Warning("ran", id="test.W001")]

    assert [m.id for m in check(None, databases=["default"])] == ["test.W001"]
    _unapply_one("manager")
    assert check(None, databases=["default"]) == []

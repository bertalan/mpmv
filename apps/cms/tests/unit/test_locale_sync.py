"""CMS.1: the locale sync after `migrate` writes to the database that was migrated."""

from types import SimpleNamespace
from typing import Any

import pytest
from django.utils.connection import ConnectionDoesNotExist
from wagtail.models import Locale

from apps.cms import apps as cms_apps
from apps.cms import services


def test_sync_locales_queries_the_database_it_is_given() -> None:
    with pytest.raises(ConnectionDoesNotExist):
        services.sync_locales(using="no_such_database")


def test_the_post_migrate_hook_syncs_the_migrated_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    introspection = SimpleNamespace(table_names=lambda: [Locale._meta.db_table])
    monkeypatch.setattr(
        cms_apps, "connections", {"replica": SimpleNamespace(introspection=introspection)}
    )
    synced: list[str] = []

    def sync(using: str = "default", **kwargs: Any) -> list[str]:
        synced.append(using)
        return []

    monkeypatch.setattr(services, "sync_locales", sync)
    cms_apps._sync_locales(using="replica")
    assert synced == ["replica"]

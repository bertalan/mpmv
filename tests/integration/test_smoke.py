"""Smoke tests against the real runtime: Django + PostgreSQL (ARC.3)."""

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import Client


def test_all_bounded_context_apps_are_installed() -> None:
    labels = {config.label for config in apps.get_app_configs()}
    expected = {app.removeprefix("apps.") for app in settings.LOCAL_APPS}
    assert expected <= labels


@pytest.mark.django_db
def test_database_is_postgresql() -> None:
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_healthz_returns_ok(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

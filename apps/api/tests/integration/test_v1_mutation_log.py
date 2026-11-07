"""CORE.7: every mutating API call of a known client is logged: who, which key, what, outcome."""

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from apps.api.models import ApiMutationLog
from apps.api.tests.factories import ApiClientFactory, ApiMutationLogFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

ECHO = "/api/v1-test/test/echo"
PAYLOAD = {"name": "Item", "quantity": 2}


def _post(client: Client, key: str | None, query: str = "") -> object:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return client.post(
        f"{ECHO}{query}", PAYLOAD, "application/json", headers=headers, REMOTE_ADDR="203.0.113.7"
    )


def test_applied_call_is_logged_with_client_key_and_outcome(client: Client) -> None:
    api_client, key = ApiClientFactory.create_with_key(name="Feed importer", scopes=["test:write"])
    response = _post(client, key)

    entry = ApiMutationLog.objects.get()
    assert entry.client_public_id == api_client.public_id
    assert entry.client_name == "Feed importer"
    assert entry.key_prefix == api_client.key_prefix
    assert entry.method == "POST"
    assert entry.path == ECHO
    assert entry.route == "api/v1-test/test/echo"
    assert entry.dry_run is False
    assert entry.status_code == 200
    assert entry.ip == "203.0.113.7"
    assert entry.request_id == response["X-Request-ID"]  # type: ignore[index]
    assert entry.duration_ms >= 0


def test_dry_run_calls_are_logged_as_dry_run(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["test:write"])
    _post(client, key, "?dry_run=true")
    assert ApiMutationLog.objects.get().dry_run is True


def test_refused_call_of_a_known_client_is_logged(client: Client) -> None:
    api_client, key = ApiClientFactory.create_with_key(scopes=["stock:write"])
    _post(client, key)
    entry = ApiMutationLog.objects.get()
    assert entry.status_code == 403
    assert entry.key_prefix == api_client.key_prefix


def test_unauthenticated_calls_are_not_stored(client: Client) -> None:
    """Anonymous traffic must not be able to fill the table; it goes to the application log."""
    _post(client, None)
    _, key = ApiClientFactory.create_with_key(expired=True)
    _post(client, key)
    assert not ApiMutationLog.objects.exists()


def test_read_calls_are_not_logged(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    client.get("/api/v1/me", headers={"Authorization": f"Bearer {key}"})
    assert not ApiMutationLog.objects.exists()


def test_mutation_log_is_append_only() -> None:
    entry = ApiMutationLogFactory.create()
    entry.status_code = 200
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(DatabaseError), transaction.atomic():
        ApiMutationLog.objects.filter(pk=entry.pk).update(status_code=200)
    with pytest.raises(DatabaseError), transaction.atomic():
        ApiMutationLog.objects.filter(pk=entry.pk).delete()

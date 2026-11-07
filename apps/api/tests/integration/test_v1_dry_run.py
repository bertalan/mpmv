"""CORE.7: `?dry_run=true` routes to the service with dry_run=True and returns the ChangeSet."""

import pytest
from django.test import Client

from apps.api.models import ApiClient, ApiMutationLog
from apps.api.tests.factories import ApiClientFactory
from apps.core.models import AuditLog

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]


def _revoke_url(target: ApiClient, query: str = "") -> str:
    return f"/api/v1-test/test/clients/{target.public_id}/revoke{query}"


@pytest.fixture
def caller() -> tuple[ApiClient, dict[str, str]]:
    api_client, key = ApiClientFactory.create_with_key(
        name="Ops pipeline", scopes=["clients:write"]
    )
    return api_client, {"Authorization": f"Bearer {key}"}


@pytest.mark.parametrize("flag", ["true", "1"])
def test_dry_run_returns_the_changeset_and_changes_nothing(
    client: Client, caller: tuple[ApiClient, dict[str, str]], flag: str
) -> None:
    target = ApiClientFactory.create(name="Old importer")
    response = client.post(_revoke_url(target, f"?dry_run={flag}"), headers=caller[1])

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["summary"] == {"create": 0, "update": 1, "delete": 0}
    [change] = body["changes"]
    assert change["op"] == "update"
    assert change["model"] == "api.apiclient"
    assert change["key"] == str(target.public_id)
    assert change["fields"]["is_active"] == [True, False]
    target.refresh_from_db()
    assert target.is_active
    assert not AuditLog.objects.exists()


def test_without_dry_run_the_change_is_applied_and_audited_with_the_key(
    client: Client, caller: tuple[ApiClient, dict[str, str]]
) -> None:
    api_client, headers = caller
    target = ApiClientFactory.create(name="Old importer")
    response = client.post(_revoke_url(target), headers=headers, REMOTE_ADDR="203.0.113.7")

    assert response.status_code == 200
    assert response.json()["dry_run"] is False
    assert response.json()["summary"]["update"] == 1
    target.refresh_from_db()
    assert not target.is_active
    entry = AuditLog.objects.get()
    assert entry.source == AuditLog.Source.API
    assert entry.action == "api.client.revoked"
    assert entry.target_key == str(target.public_id)
    assert api_client.key_prefix in entry.actor_label
    assert "Ops pipeline" in entry.actor_label
    assert entry.ip == "203.0.113.7"
    assert entry.request_id == response["X-Request-ID"]


def test_dry_run_false_applies(client: Client, caller: tuple[ApiClient, dict[str, str]]) -> None:
    target = ApiClientFactory.create()
    response = client.post(_revoke_url(target, "?dry_run=false"), headers=caller[1])
    assert response.json()["dry_run"] is False
    target.refresh_from_db()
    assert not target.is_active


def test_invalid_dry_run_value_is_422_and_changes_nothing(
    client: Client, caller: tuple[ApiClient, dict[str, str]]
) -> None:
    target = ApiClientFactory.create()
    response = client.post(_revoke_url(target, "?dry_run=maybe"), headers=caller[1])
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["query", "dry_run"]
    target.refresh_from_db()
    assert target.is_active
    assert not AuditLog.objects.exists()
    assert ApiMutationLog.objects.get().status_code == 422

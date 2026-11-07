"""CORE.7: API v1 authentication with CORE.6 keys, scopes per operation, RFC 9457 refusals."""

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

ME = "/api/v1/me"
SCOPED = "/api/v1-test/test/throttled"  # requires test:read


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_me_returns_the_calling_client_and_its_scopes(client: Client) -> None:
    api_client, key = ApiClientFactory.create_with_key(
        name="Feed importer", scopes=["stock:write", "catalog:write"]
    )
    response = client.get(ME, headers=_bearer(key))
    assert response.status_code == 200
    body = response.json()
    assert body["public_id"] == str(api_client.public_id)
    assert body["name"] == "Feed importer"
    assert body["key_prefix"] == api_client.key_prefix
    assert body["scopes"] == ["catalog:write", "stock:write"]
    assert body["expires_at"].startswith(api_client.expires_at.date().isoformat())
    assert "key_hash" not in body


def test_me_is_open_to_any_valid_key_whatever_its_scopes(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["reports:read"])
    assert client.get(ME, headers=_bearer(key)).status_code == 200


def test_missing_credentials_are_a_401_problem_with_a_bearer_challenge(client: Client) -> None:
    response = client.get(ME)
    assert response.status_code == 401
    assert response["Content-Type"] == "application/problem+json"
    assert response["WWW-Authenticate"] == 'Bearer realm="api"'
    body = response.json()
    assert body["status"] == 401
    assert body["code"] == "missing_credentials"
    assert body["type"] == "urn:mpmv:problem:missing_credentials"
    assert body["instance"] == ME


@pytest.mark.parametrize(
    ("traits", "code"), [({"expired": True}, "expired"), ({"revoked": True}, "revoked")]
)
def test_expired_or_revoked_keys_are_401(
    client: Client, traits: dict[str, bool], code: str
) -> None:
    _, key = ApiClientFactory.create_with_key(**traits)
    response = client.get(ME, headers=_bearer(key))
    assert response.status_code == 401
    assert 'error="invalid_token"' in response["WWW-Authenticate"]
    assert response.json()["code"] == code


def test_unknown_key_is_401_invalid_key(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    tampered = key[:-4] + ("AAAA" if not key.endswith("AAAA") else "BBBB")
    response = client.get(ME, headers=_bearer(tampered))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_key"


def test_out_of_scope_key_is_a_403_problem_naming_the_scope(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["stock:write"])
    response = client.get(SCOPED, headers=_bearer(key))
    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response["WWW-Authenticate"] == (
        'Bearer realm="api", error="insufficient_scope", scope="test:read"'
    )
    body = response.json()
    assert body["code"] == "insufficient_scope"
    assert body["required_scope"] == "test:read"


def test_ip_outside_the_allow_list_is_403(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(allowed_ips=["198.51.100.0/24"])
    denied = client.get(ME, headers=_bearer(key), REMOTE_ADDR="203.0.113.7")
    allowed = client.get(ME, headers=_bearer(key), REMOTE_ADDR="198.51.100.9")
    assert denied.status_code == 403
    assert denied.json()["code"] == "ip_not_allowed"
    assert allowed.status_code == 200


def test_bearer_mutations_need_no_csrf_token() -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["test:write"])
    csrf_client = Client(enforce_csrf_checks=True)
    response = csrf_client.post(
        "/api/v1-test/test/echo",
        {"name": "Item", "quantity": 2},
        content_type="application/json",
        headers=_bearer(key),
    )
    assert response.status_code == 200


def test_every_response_carries_a_request_id(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key()
    ok = client.get(ME, headers=_bearer(key))
    refused = client.get(ME)
    assert len(ok["X-Request-ID"]) == 32
    assert refused["X-Request-ID"] == refused.json()["request_id"]
    assert ok["X-Request-ID"] != refused["X-Request-ID"]

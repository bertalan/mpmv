"""CORE.6 done-when: expired or out-of-scope key → 401/403, through a real Django view.

Since CORE.7 the refusals are RFC 9457 problem details, as in API v1.
"""

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls")]

URL = "/protected/catalog"


def test_valid_key_reaches_the_view(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(name="Feed importer")
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert response.json() == {"client": "Feed importer"}


def test_missing_credentials_are_401_with_a_bearer_challenge(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["status"] == 401
    assert response.json()["code"] == "missing_credentials"


def test_expired_key_is_401(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(expired=True)
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response["WWW-Authenticate"]
    assert response.json()["code"] == "expired"


def test_revoked_key_is_401(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(revoked=True)
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 401
    assert response.json()["code"] == "revoked"


def test_out_of_scope_key_is_403(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["stock:write"])
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response["WWW-Authenticate"]
    assert 'scope="catalog:write"' in response["WWW-Authenticate"]
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "insufficient_scope"
    assert response.json()["required_scope"] == "catalog:write"


def test_ip_outside_the_allow_list_is_403(client: Client) -> None:
    _, key = ApiClientFactory.create_with_key(allowed_ips=["198.51.100.0/24"])
    headers = {"Authorization": f"Bearer {key}"}
    denied = client.get(URL, headers=headers, REMOTE_ADDR="203.0.113.7")
    allowed = client.get(URL, headers=headers, REMOTE_ADDR="198.51.100.9")
    assert denied.status_code == 403
    assert denied.json()["code"] == "ip_not_allowed"
    assert allowed.status_code == 200

"""CORE.6 done-when: expired or out-of-scope key → 401/403, through a real Django view."""

import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from apps.api.models import ApiClient
from apps.api.services import revoke_api_client

from .conftest import CTX, IssueFn

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls")]

URL = "/protected/catalog"


def test_valid_key_reaches_the_view(client: Client, issue: IssueFn) -> None:
    _, key = issue(name="Feed importer")
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert response.json() == {"client": "Feed importer"}


def test_missing_credentials_are_401_with_a_bearer_challenge(client: Client) -> None:
    response = client.get(URL)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")
    assert response.json() == {"error": "unauthenticated", "reason": "missing_credentials"}


def test_expired_key_is_401(client: Client, issue: IssueFn) -> None:
    api_client, key = issue()
    ApiClient.objects.filter(pk=api_client.pk).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response["WWW-Authenticate"]
    assert response.json() == {"error": "unauthenticated", "reason": "expired"}


def test_revoked_key_is_401(client: Client, issue: IssueFn) -> None:
    api_client, key = issue()
    revoke_api_client(api_client, ctx=CTX)
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 401
    assert response.json()["reason"] == "revoked"


def test_out_of_scope_key_is_403(client: Client, issue: IssueFn) -> None:
    _, key = issue(scopes=["stock:write"])
    response = client.get(URL, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response["WWW-Authenticate"]
    assert 'scope="catalog:write"' in response["WWW-Authenticate"]
    assert response.json() == {"error": "forbidden", "reason": "insufficient_scope"}


def test_ip_outside_the_allow_list_is_403(client: Client, issue: IssueFn) -> None:
    _, key = issue(allowed_ips=["198.51.100.0/24"])
    headers = {"Authorization": f"Bearer {key}"}
    denied = client.get(URL, headers=headers, REMOTE_ADDR="203.0.113.7")
    allowed = client.get(URL, headers=headers, REMOTE_ADDR="198.51.100.9")
    assert denied.status_code == 403
    assert denied.json()["reason"] == "ip_not_allowed"
    assert allowed.status_code == 200

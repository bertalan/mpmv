"""CORE.29 (owner decision Q-13): the API documentation is public locally and on STAGE; on LIVE
it answers 404 to anybody but a valid API key or a signed-in staff member."""

import datetime as dt

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.api.tests.factories import ApiClientFactory
from apps.core.models import Throttle

pytestmark = pytest.mark.django_db

DOCS = "/api/v1/docs"
SCHEMA = "/api/v1/openapi.json"
LIVE = {"API_DOCS_PUBLIC": False}


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.parametrize("url", [DOCS, SCHEMA])
def test_docs_are_open_where_the_setting_says_so(client: Client, url: str) -> None:
    with override_settings(API_DOCS_PUBLIC=True):
        assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", [DOCS, SCHEMA])
@override_settings(**LIVE)
def test_live_answers_404_to_an_anonymous_request(client: Client, url: str) -> None:
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("url", [DOCS, SCHEMA])
@override_settings(**LIVE)
def test_live_answers_200_to_a_valid_key(client: Client, url: str) -> None:
    _, key = ApiClientFactory.create_with_key(scopes=["reports:read"])
    assert client.get(url, headers=_bearer(key)).status_code == 200


@pytest.mark.parametrize("url", [DOCS, SCHEMA])
@override_settings(**LIVE)
def test_live_answers_404_to_a_refused_key(client: Client, url: str) -> None:
    api_client, key = ApiClientFactory.create_with_key()
    api_client.expires_at = timezone.now() - dt.timedelta(days=1)
    api_client.save()
    assert client.get(url, headers=_bearer(key)).status_code == 404
    assert client.get(url, headers=_bearer(key + "x")).status_code == 404


@pytest.mark.parametrize("url", [DOCS, SCHEMA])
@override_settings(**LIVE)
def test_live_answers_200_to_signed_in_staff(client: Client, url: str) -> None:
    client.force_login(UserFactory.create(staff=True))
    assert client.get(url).status_code == 200


@override_settings(**LIVE)
def test_live_answers_404_to_a_signed_in_customer(client: Client) -> None:
    client.force_login(UserFactory.create())
    assert client.get(DOCS).status_code == 404


@override_settings(**LIVE)
def test_a_refused_key_on_the_docs_counts_against_the_ip_limit(client: Client) -> None:
    """Guessing keys at the docs URL is as costly as guessing them at the API (CORE.20)."""
    client.get(DOCS, headers=_bearer("mpmv_bad_key"))
    assert Throttle.objects.exists()

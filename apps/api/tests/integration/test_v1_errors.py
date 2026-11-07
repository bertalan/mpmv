"""CORE.7: every API v1 error is an RFC 9457 `application/problem+json` document."""

import logging

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.api.tests.integration.v1_api import LEAKY_DETAIL

pytestmark = [pytest.mark.django_db, pytest.mark.urls("apps.api.tests.integration.urls_v1")]

BASE = "/api/v1-test/test"


@pytest.fixture
def headers() -> dict[str, str]:
    _, key = ApiClientFactory.create_with_key(scopes=["test:read", "test:write"])
    return {"Authorization": f"Bearer {key}"}


def _problem(response: object, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status  # type: ignore[attr-defined]
    assert response["Content-Type"] == "application/problem+json"  # type: ignore[index]
    body: dict[str, object] = response.json()  # type: ignore[attr-defined]
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == f"urn:mpmv:problem:{code}"
    assert isinstance(body["title"], str)
    assert body["title"]
    return body


def test_malformed_json_body_is_400(client: Client, headers: dict[str, str]) -> None:
    response = client.post(
        f"{BASE}/echo", "{not json", content_type="application/json", headers=headers
    )
    _problem(response, 400, "bad_request")


def test_http404_is_a_404_problem_without_internal_detail(
    client: Client, headers: dict[str, str]
) -> None:
    body = _problem(client.get(f"{BASE}/errors/http404", headers=headers), 404, "not_found")
    assert LEAKY_DETAIL not in str(body)


def test_missing_object_is_404(client: Client, headers: dict[str, str]) -> None:
    _problem(client.get(f"{BASE}/errors/does-not-exist", headers=headers), 404, "not_found")


def test_unknown_api_path_is_a_404_problem(client: Client) -> None:
    body = _problem(client.get("/api/v1/no-such-resource"), 404, "not_found")
    assert body["instance"] == "/api/v1/no-such-resource"


def test_permission_denied_is_403(client: Client, headers: dict[str, str]) -> None:
    body = _problem(client.get(f"{BASE}/errors/permission", headers=headers), 403, "forbidden")
    assert LEAKY_DETAIL not in str(body)


def test_integrity_error_is_409_without_database_detail(
    client: Client, headers: dict[str, str]
) -> None:
    body = _problem(client.get(f"{BASE}/errors/integrity", headers=headers), 409, "conflict")
    assert LEAKY_DETAIL not in str(body)


def test_explicit_problem_keeps_its_code_and_extensions(
    client: Client, headers: dict[str, str]
) -> None:
    body = _problem(client.get(f"{BASE}/errors/problem", headers=headers), 409, "already_exists")
    assert body["detail"] == "SKU A-1 already exists."
    assert body["sku"] == "A-1"


def test_body_validation_is_422_with_field_errors(client: Client, headers: dict[str, str]) -> None:
    response = client.post(
        f"{BASE}/echo", {"name": "Item", "quantity": 0}, "application/json", headers=headers
    )
    body = _problem(response, 422, "validation_error")
    errors = body["errors"]
    assert isinstance(errors, list)
    assert [e["loc"][-1] for e in errors] == ["quantity"]
    assert set(errors[0]) == {"loc", "msg", "type"}


def test_django_validation_error_is_422(client: Client, headers: dict[str, str]) -> None:
    response = client.get(f"{BASE}/errors/django-validation", headers=headers)
    body = _problem(response, 422, "validation_error")
    assert body["errors"] == [{"loc": ["name"], "msg": "Name is too long.", "type": "invalid"}]


def test_rate_limit_is_429_with_retry_after(client: Client, headers: dict[str, str]) -> None:
    assert client.get(f"{BASE}/throttled", headers=headers).status_code == 200
    response = client.get(f"{BASE}/throttled", headers=headers)
    _problem(response, 429, "rate_limited")
    assert int(response["Retry-After"]) > 0


def test_unexpected_exception_is_a_logged_500_without_internals(
    client: Client, headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="apps.api"):
        response = client.get(f"{BASE}/errors/crash", headers=headers)
    body = _problem(response, 500, "internal_error")
    assert LEAKY_DETAIL not in response.content.decode()
    assert "Traceback" not in response.content.decode()
    assert any(LEAKY_DETAIL in r.getMessage() or r.exc_info for r in caplog.records)
    assert body["request_id"] == response["X-Request-ID"]

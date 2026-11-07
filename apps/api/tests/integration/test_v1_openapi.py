"""CORE.7 done-when: the OpenAPI spec is published and valid; the API contract is enforced.

STAGE does not exist yet: the schema is proven here (served and validated) and exported by CI.
"""

import re
from typing import Any

import pytest
from django.core.checks import Error
from django.http import HttpRequest
from django.test import Client
from ninja import Router
from openapi_spec_validator import validate

from apps.api.v1.api import api, create_api
from apps.api.v1.checks import check_api
from apps.api.v1.security import ApiKey

pytestmark = pytest.mark.django_db

SCHEMA_URL = "/api/v1/openapi.json"
MUTATING = {"post", "put", "patch", "delete"}


@pytest.fixture
def schema(client: Client) -> dict[str, Any]:
    response = client.get(SCHEMA_URL)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    body: dict[str, Any] = response.json()
    return body


def test_schema_is_served_and_is_valid_openapi_3_1(schema: dict[str, Any]) -> None:
    validate(schema)
    assert schema["openapi"].startswith("3.1")
    assert schema["info"]["version"] == "1"
    assert "/api/v1/me" in schema["paths"]


def test_bearer_api_key_scheme_and_scopes_per_operation(schema: dict[str, Any]) -> None:
    scheme = schema["components"]["securitySchemes"]["ApiKey"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/me"]["get"]["security"] == [{"ApiKey": []}]


def test_errors_are_documented_as_problem_details(schema: dict[str, Any]) -> None:
    problem = schema["components"]["schemas"]["Problem"]
    assert {"type", "title", "status", "code"} <= set(problem["required"])
    default = schema["paths"]["/api/v1/me"]["get"]["responses"]["default"]
    assert default["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }


@pytest.mark.urls("apps.api.tests.integration.urls_v1")
def test_test_api_schema_shows_dry_run_and_required_scopes(client: Client) -> None:
    schema = client.get("/api/v1-test/openapi.json").json()
    validate(schema)
    revoke = schema["paths"]["/api/v1-test/test/clients/{public_id}/revoke"]["post"]
    assert revoke["security"] == [{"ApiKey": ["clients:write"]}]
    dry_run = next(p for p in revoke["parameters"] if p["name"] == "dry_run")
    assert dry_run["in"] == "query"
    assert dry_run["schema"]["type"] == "boolean"
    assert dry_run["schema"]["default"] is False


def test_docs_page_is_served_under_the_strict_csp(client: Client) -> None:
    response = client.get("/api/v1/docs")
    assert response.status_code == 200
    policy = response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy
    html = response.content.decode()
    executable = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", html)
    assert executable == [], "inline scripts would be blocked by the CSP"
    assert "cdn." not in html  # assets are served by us, not by a third-party CDN


def test_production_api_passes_its_contract_checks() -> None:
    assert check_api(api) == []


def test_every_production_operation_is_secured_and_mutations_accept_dry_run(
    schema: dict[str, Any],
) -> None:
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert operation.get("security"), f"{method.upper()} {path} has no API key auth"
            if method in MUTATING:
                names = {p["name"] for p in operation.get("parameters", [])}
                assert "dry_run" in names, f"{method.upper()} {path} has no dry_run"


def test_contract_check_flags_unsecured_operations_and_mutations_without_dry_run() -> None:
    router = Router()

    @router.get("/open")
    def open_read(request: HttpRequest) -> dict[str, str]:
        return {}

    @router.post("/write", auth=ApiKey("test:write"))
    def write_without_dry_run(request: HttpRequest) -> dict[str, str]:
        return {}

    bad_api = create_api(urls_namespace="api-v1-contract-check")
    bad_api.add_router("", router)
    errors = check_api(bad_api)
    assert all(isinstance(e, Error) for e in errors)
    assert sorted(e.id for e in errors) == ["api.E001", "api.E002"]
    assert any("/open" in e.msg for e in errors)
    assert any("/write" in e.msg for e in errors)

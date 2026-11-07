"""CORE.7: RFC 9457 problem details, built without Django or the database."""

import http

import pytest

from apps.api.engine import AuthStatus, Decision, Reason, deny
from apps.api.problems import (
    PROBLEM_CONTENT_TYPE,
    Problem,
    auth_problem,
    problem,
    validation_problem,
)


def test_content_type_is_the_rfc_9457_media_type() -> None:
    assert PROBLEM_CONTENT_TYPE == "application/problem+json"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "unauthenticated"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (409, "conflict"),
        (422, "validation_error"),
        (429, "rate_limited"),
        (500, "internal_error"),
    ],
)
def test_default_code_and_title_per_status(status: int, code: str) -> None:
    body = problem(status).to_dict()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == f"urn:mpmv:problem:{code}"
    assert body["title"] == http.HTTPStatus(status).phrase


def test_detail_instance_and_extensions_are_rendered() -> None:
    built = problem(409, code="already_exists", detail="SKU A-1 exists.", sku="A-1")
    body = built.to_dict(instance="/api/v1/products", request_id="abc123")
    assert body == {
        "type": "urn:mpmv:problem:already_exists",
        "title": "Conflict",
        "status": 409,
        "detail": "SKU A-1 exists.",
        "instance": "/api/v1/products",
        "code": "already_exists",
        "sku": "A-1",
        "request_id": "abc123",
    }


def test_empty_detail_and_instance_are_omitted() -> None:
    assert set(problem(404).to_dict()) == {"type", "title", "status", "code"}


def test_extensions_cannot_override_standard_members() -> None:
    with pytest.raises(ValueError, match="type"):
        problem(400, type="about:blank")
    with pytest.raises(ValueError, match="status"):
        problem(400).to_dict(status=200)


def test_problem_is_immutable() -> None:
    built = problem(404)
    assert isinstance(built, Problem)
    with pytest.raises(AttributeError):
        built.status = 200  # type: ignore[misc]


def test_validation_problem_keeps_location_message_and_type_only() -> None:
    errors = [
        {
            "loc": ["body", "payload", "quantity"],
            "msg": "Input should be greater than 0",
            "type": "greater_than",
            "input": "mpmv_deadbeef_should-never-be-echoed",
            "ctx": {"gt": 0},
        }
    ]
    body = validation_problem(errors).to_dict()
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert body["errors"] == [
        {
            "loc": ["body", "payload", "quantity"],
            "msg": "Input should be greater than 0",
            "type": "greater_than",
        }
    ]
    assert "should-never-be-echoed" not in repr(body)


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        (Reason.MISSING, 401),
        (Reason.MALFORMED, 401),
        (Reason.INVALID, 401),
        (Reason.REVOKED, 401),
        (Reason.EXPIRED, 401),
        (Reason.IP_NOT_ALLOWED, 403),
        (Reason.INSUFFICIENT_SCOPE, 403),
    ],
)
def test_auth_problem_uses_the_reason_as_code(reason: Reason, status: int) -> None:
    body = auth_problem(deny(reason), "catalog:write").to_dict()
    assert body["status"] == status
    assert body["code"] == str(reason)
    assert body["detail"]


def test_insufficient_scope_names_the_required_scope() -> None:
    body = auth_problem(deny(Reason.INSUFFICIENT_SCOPE), "catalog:write").to_dict()
    assert body["required_scope"] == "catalog:write"
    assert "required_scope" not in auth_problem(deny(Reason.EXPIRED), "catalog:write").to_dict()


def test_auth_problem_refuses_an_allowed_decision() -> None:
    with pytest.raises(ValueError, match="allowed"):
        auth_problem(Decision(AuthStatus.OK, None), "catalog:write")

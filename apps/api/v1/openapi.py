"""OpenAPI schema for API v1: required scopes and RFC 9457 error responses (CORE.7)."""

from typing import Any

from ninja import Schema
from ninja.openapi.schema import OpenAPISchema
from ninja.operation import Operation
from pydantic import Field

from apps.api.problems import PROBLEM_CONTENT_TYPE

from .security import ApiKey

PROBLEM_REF = "#/components/schemas/Problem"


class ProblemOut(Schema):
    """RFC 9457 problem details. Further members depend on `code` (e.g. `errors`)."""

    model_config = {"extra": "allow"}

    type: str = Field(description="URI identifying the problem type: urn:mpmv:problem:<code>.")
    title: str = Field(description="Short summary of the problem type.")
    status: int = Field(description="HTTP status code.")
    code: str = Field(description="Stable machine-readable problem code.")
    detail: str | None = Field(None, description="Explanation specific to this occurrence.")
    instance: str | None = Field(None, description="Request path of this occurrence.")
    request_id: str | None = Field(None, description="Id to quote when reporting the error.")


def problem_component() -> dict[str, Any]:
    schema = ProblemOut.model_json_schema()
    schema["title"] = "Problem"
    return schema


class ApiV1Schema(OpenAPISchema):
    """Ninja's schema plus the scope of each operation and a problem `default` response.

    OpenAPI 3.1 lets a non-OAuth security requirement list role names: we list the API key
    scope the operation requires (empty for operations open to any valid key).
    """

    def operation_security(self, operation: Operation) -> list[dict[str, Any]] | None:
        result = super().operation_security(operation)
        if not result:
            return result
        scoped = []
        for auth in operation.auth_callbacks:
            if isinstance(auth, ApiKey):
                scoped.append({type(auth).__name__: auth.scopes})
        return scoped or result

    def responses(self, operation: Operation) -> dict[Any, dict[str, Any]]:
        result: dict[Any, dict[str, Any]] = dict(super().responses(operation))
        result["default"] = {
            "description": "Error, as RFC 9457 problem details",
            "content": {PROBLEM_CONTENT_TYPE: {"schema": {"$ref": PROBLEM_REF}}},
        }
        return result

    def get_components(self) -> dict[str, Any]:
        components: dict[str, Any] = super().get_components()
        components["schemas"]["Problem"] = problem_component()
        return components

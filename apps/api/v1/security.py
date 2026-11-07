"""API key authentication for API v1: CORE.6 keys, one required scope per operation."""

from typing import Any

from django.http import HttpRequest
from ninja.security.base import AuthBase

from apps.api.auth import authenticate_request, remember_client
from apps.api.services import AuthResult


class ApiAuthError(Exception):
    """A refused API key; rendered as a 401/403 problem by `apps.api.v1.errors`."""

    def __init__(self, result: AuthResult, required_scope: str | None) -> None:
        super().__init__(str(result.reason))
        self.result = result
        self.required_scope = required_scope


class ApiKey(AuthBase):
    """`Authorization: Bearer mpmv_<prefix>_<secret>` checked against the operation's scope.

    Pass the scope the operation needs, e.g. `auth=ApiKey("catalog:write")`. `ApiKey(None)`
    accepts any valid key: only for operations about the key itself, such as `GET /me`.
    The scope is published in the operation's OpenAPI `security` requirement.
    """

    openapi_type = "http"
    openapi_scheme = "bearer"
    openapi_bearerFormat = "mpmv_<prefix>_<secret>"
    openapi_description = "API key issued to an ApiClient; each operation requires one scope."

    def __init__(self, scope: str | None) -> None:
        self.scope = scope
        super().__init__()

    @property
    def scopes(self) -> list[str]:
        return [] if self.scope is None else [self.scope]

    def __call__(self, request: HttpRequest) -> Any:
        result = authenticate_request(request, self.scope)
        remember_client(request, result.client)
        if not result.ok:
            raise ApiAuthError(result, self.scope)
        return result.client

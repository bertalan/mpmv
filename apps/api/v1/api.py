"""The API v1 instance (CORE.7): `create_api()` holds every cross-cutting rule.

- Auth: CORE.6 API keys, one scope per operation (`apps.api.v1.security.ApiKey`).
- Errors: RFC 9457 problem details (`apps.api.v1.errors`).
- Dry-run and the mutation log (`apps.api.v1.mutations`), enforced by `apps.api.v1.checks`.
- OpenAPI 3.1 at /api/v1/openapi.json, interactive docs at /api/v1/docs (Swagger UI from our
  own static files, no inline script, so the strict CSP of SEC.1 applies unchanged).

Tests build their own instance with `create_api()` so they exercise the same setup.
"""

from typing import Any

from ninja import NinjaAPI
from ninja.openapi.docs import Swagger

from .catalog import router as catalog_router
from .endpoints import router as v1_router
from .errors import install_problem_handlers
from .mutations import track_calls
from .openapi import ApiV1Schema

TITLE = "MPMV API"
VERSION = "1"
DESCRIPTION = (
    "Machine API for pipelines, integrations and agents. Authenticate with "
    "`Authorization: Bearer <API key>`; each operation requires the scope listed in its "
    "security requirement. Mutating operations accept `?dry_run=true` and return the "
    "ChangeSet without applying it. Errors are RFC 9457 problem details "
    "(`application/problem+json`).\n\n"
    "Refused keys are limited per client IP (IPv6 by /64): by default, after 30 answers 401 "
    "in a minute the IP is blocked for 5 minutes. While it is blocked every request from it "
    "gets 429, even one with a valid key, because the block is checked before the key. "
    "An address shared behind a NAT can therefore be blocked by someone else's bad keys; "
    "wait for the seconds in `Retry-After` and retry."
)


class ApiV1(NinjaAPI):
    def get_openapi_schema(
        self, *, path_prefix: str | None = None, path_params: dict[str, Any] | None = None
    ) -> ApiV1Schema:
        if path_prefix is None:
            path_prefix = self.get_root_path(path_params or {})
        return ApiV1Schema(self, path_prefix)


def create_api(*, urls_namespace: str = "api-v1") -> ApiV1:
    """A configured, empty API v1; add routers to it."""
    api = ApiV1(
        title=TITLE,
        version=VERSION,
        description=DESCRIPTION,
        urls_namespace=urls_namespace,
        docs=Swagger(settings={"persistAuthorization": False}),
    )
    install_problem_handlers(api)
    api.add_decorator(track_calls, mode="view")
    return api


api = create_api()
api.add_router("", v1_router)
api.add_router("/catalog", catalog_router)

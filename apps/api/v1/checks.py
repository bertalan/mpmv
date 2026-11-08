"""System checks for the API v1 contract, run by `manage.py check` (and so by `make check`).

- api.E001: every operation requires an API key (no anonymous operation).
- api.E002: every mutating operation declares the `dry_run` query parameter, so a client's
  `?dry_run=true` is never silently ignored by an operation that would then apply changes.
"""

from typing import Any

from django.core.checks import CheckMessage, Error, Tags, register
from ninja import NinjaAPI

from apps.api.engine import MUTATING_METHODS

from .mutations import DRY_RUN_PARAM


def check_api(api: NinjaAPI) -> list[CheckMessage]:
    schema = api.get_openapi_schema(path_prefix="")
    errors: list[CheckMessage] = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            label = f"{method.upper()} {path}"
            if not operation.get("security"):
                errors.append(
                    Error(
                        f"{label} has no API key authentication.",
                        hint="Pass auth=ApiKey('<resource>:<action>') to the operation.",
                        id="api.E001",
                    )
                )
            if method.upper() in MUTATING_METHODS and not _declares_dry_run(operation):
                errors.append(
                    Error(
                        f"{label} is mutating but does not accept ?{DRY_RUN_PARAM}=.",
                        hint="Declare `dry_run: DryRun = False` and pass it to the service.",
                        id="api.E002",
                    )
                )
    return errors


def _declares_dry_run(operation: dict[str, Any]) -> bool:
    return any(
        p.get("in") == "query" and p.get("name") == DRY_RUN_PARAM
        for p in operation.get("parameters", [])
    )


@register(Tags.urls)
def check_api_v1(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    from .api import api

    return check_api(api)

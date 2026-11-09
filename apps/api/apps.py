from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "apps.api"
    label = "api"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("apiclient",)
    unaudited_models = {
        "apimutationlog": "the API call log itself (append-only)",
        "apiratecounter": "per-key request counters of the rate limit, not a domain change",
        "apirefusedkey": "per-IP log of refused keys for the CORE.20 limit, not a domain change",
    }

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers the rate limit settings check)
        from .v1 import checks as v1_checks  # noqa: F401  (registers the API contract checks)

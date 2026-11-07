from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "apps.api"
    label = "api"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("apiclient",)
    unaudited_models = {"apimutationlog": "the API call log itself (append-only)"}

    def ready(self) -> None:
        from .v1 import checks  # noqa: F401  (registers the API contract system checks)

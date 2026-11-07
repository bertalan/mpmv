from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "apps.api"
    label = "api"

    def ready(self) -> None:
        from .v1 import checks  # noqa: F401  (registers the API contract system checks)

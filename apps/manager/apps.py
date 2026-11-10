from django.apps import AppConfig
from django.core import checks


class ManagerConfig(AppConfig):
    """The Manager backoffice (CORE.17, spec 02 §Backoffice): an adapter like `api`, with views,
    templates and the navigation registry, and no models or business logic."""

    name = "apps.manager"
    label = "manager"

    def ready(self) -> None:
        from .checks import check_gate_installed, check_manager_urls
        from .navigation import registry
        from .sections import register_built_in

        register_built_in(registry)
        checks.register(check_gate_installed, checks.Tags.security)
        checks.register(check_manager_urls, checks.Tags.urls)

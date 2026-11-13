from django.apps import AppConfig
from django.core import checks


class ManagerConfig(AppConfig):
    """The Manager backoffice (CORE.17, spec 02 §Backoffice): an adapter like `api`, with views,
    templates and the navigation registry. No tables or business logic: one unmanaged model only
    carries the role permissions (CORE.18)."""

    unaudited_models = {"platformaccess": "unmanaged, no table: only carries permissions"}

    name = "apps.manager"
    label = "manager"

    def ready(self) -> None:
        from apps.core import flags

        from .checks import (
            check_gate_installed,
            check_manager_urls,
            check_role_drift,
            check_roles_matrix,
        )
        from .navigation import registry
        from .sections import register_built_in

        register_built_in(registry)
        if not flags.is_registered("one_person_team"):
            flags.register(
                flags.FlagSpec(
                    "one_person_team",
                    "BO-13: one person may hold incompatible platform roles (small teams). "
                    "Switched on by a superuser; shown as a permanent warning.",
                )
            )
        checks.register(check_gate_installed, checks.Tags.security)
        checks.register(check_manager_urls, checks.Tags.urls)
        checks.register(check_roles_matrix)
        checks.register(check_role_drift, checks.Tags.database)

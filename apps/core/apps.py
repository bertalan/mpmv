from django.apps import AppConfig
from django.core import checks


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from .checks import (
            check_client_ip_settings,
            check_trusted_proxy_for_deploy,
            translation_models,
        )

        checks.register(translation_models)
        checks.register(check_client_ip_settings, checks.Tags.security)
        checks.register(check_trusted_proxy_for_deploy, checks.Tags.security, deploy=True)

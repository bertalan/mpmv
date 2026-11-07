from django.apps import AppConfig
from django.core import checks


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from .checks import translation_models

        checks.register(translation_models)

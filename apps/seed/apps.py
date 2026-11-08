from django.apps import AppConfig


class SeedConfig(AppConfig):
    """The demo seed (ARC.10). No models: it writes the other apps' rows through their services.

    It sits above every other app in the dependency graph (spec 01 §1.3), and no app imports it.
    """

    name = "apps.seed"
    label = "seed"
    audited_models: tuple[str, ...] = ()
    unaudited_models: dict[str, str] = {}

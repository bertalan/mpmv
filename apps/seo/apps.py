from django.apps import AppConfig


class SeoConfig(AppConfig):
    name = "apps.seo"
    label = "seo"

    def ready(self) -> None:
        from . import signals

        signals.connect()  # SEO.6: what drops the anonymous page cache

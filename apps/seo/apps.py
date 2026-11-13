from django.apps import AppConfig


class SeoConfig(AppConfig):
    name = "apps.seo"
    label = "seo"
    audited_models = ("seosettings",)
    unaudited_models: dict[str, str] = {}

    def ready(self) -> None:
        from apps.core import indexing

        from .services import site_allows_indexing

        # core cannot import this app: it calls the site policy registered here (SEO.8 2/2).
        indexing.register_site_policy(site_allows_indexing)

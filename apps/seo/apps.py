from django.apps import AppConfig


class SeoConfig(AppConfig):
    name = "apps.seo"
    label = "seo"
    audited_models = ("seosettings",)
    unaudited_models: dict[str, str] = {}

    def ready(self) -> None:
        from apps.cms.services import register_golive_check
        from apps.core import indexing

        from . import signals
        from .services import golive_indexing_messages, site_allows_indexing

        signals.connect()  # SEO.6: what drops the anonymous page cache
        # core cannot import this app: it calls the site policy registered here (SEO.8 2/2).
        indexing.register_site_policy(site_allows_indexing)
        register_golive_check(golive_indexing_messages)

from django.apps import AppConfig
from django.core.checks import register


class SeoConfig(AppConfig):
    name = "apps.seo"
    label = "seo"
    audited_models = ("seosettings",)
    unaudited_models = {
        "consentlog": (
            "the visitor's own consent decision, itself the append-only proof log; it holds no "
            "personal data beyond a random id"
        ),
    }

    def ready(self) -> None:
        from apps.cms.services import register_golive_check
        from apps.core import indexing

        from .checks import check_consent_provider
        from .services import golive_indexing_messages, site_allows_indexing

        # core cannot import this app: it calls the site policy registered here (SEO.8 2/2).
        indexing.register_site_policy(site_allows_indexing)
        register_golive_check(golive_indexing_messages)
        register(check_consent_provider)  # SEO.9

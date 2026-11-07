from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    # Every write goes through the audited services: prices (CAT.1), edits (CAT.12),
    # duplicates to another vendor (CAT.14).
    audited_models = ("product", "variant")
    unaudited_models = {
        "producttranslation": "storefront texts under the translation workflow",
        "varianttranslation": "storefront texts under the translation workflow",
        # CAT.2: platform navigation data, no money, access or legal facts.
        "category": "navigation structure (category tree); no money, access or legal facts",
        "categorytranslation": "storefront texts under the translation workflow",
        "taxonomy": "navigation configuration (which taxonomies exist and how they behave)",
        "taxonomytranslation": "storefront texts under the translation workflow",
        "term": "navigation structure (taxonomy terms)",
        "termtranslation": "storefront texts under the translation workflow",
        "brand": "manufacturer names shown on product pages and feeds",
        "brandtranslation": "storefront texts under the translation workflow",
    }

    def ready(self) -> None:
        from . import signals  # noqa: F401  (connects the CAT.14 upsell guard)

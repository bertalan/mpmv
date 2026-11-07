from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ()
    unaudited_models = {
        "product": "catalog content; price changes are audited by services.set_product_price "
        "(CAT.1), publishing by the CAT.10 services, duplicates to another vendor by "
        "services.duplicate_product_to_vendor (CAT.14)",
        "producttranslation": "storefront texts under the translation workflow",
        "variant": "catalog content; price changes are audited by services.set_variant_price",
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

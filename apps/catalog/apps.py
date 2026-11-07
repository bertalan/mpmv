from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ()
    unaudited_models = {
        "product": "catalog content; price changes are audited by services.set_product_price "
        "(CAT.1), publishing by the CAT.10 services",
        "producttranslation": "storefront texts under the translation workflow",
        "variant": "catalog content; price changes are audited by services.set_variant_price",
        "varianttranslation": "storefront texts under the translation workflow",
    }

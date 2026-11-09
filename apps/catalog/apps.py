from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    # Every write goes through the audited services: prices (CAT.1), edits (CAT.12),
    # duplicates to another vendor (CAT.14), URL changes (CAT.5: services.sync_product_slug and
    # services.record_old_slugs).
    # CAT.6: redirects through services.save_redirect, delete_redirect and import_redirects.
    audited_models = ("product", "variant", "slughistory", "redirect")
    unaudited_models = {
        "producttranslation": "storefront texts under the translation workflow",
        "varianttranslation": "storefront texts under the translation workflow",
        # CAT.13: written only by a database trigger on SKU renames, which services audit.
        "skualias": "SKU rename history written by a database trigger; renames through "
        "services.rename_product_sku and rename_variant_sku are audited",
        # CAT.2: platform navigation data, no money, access or legal facts.
        "category": "navigation structure (category tree); no money, access or legal facts",
        "categorytranslation": "storefront texts under the translation workflow",
        "taxonomy": "navigation configuration (which taxonomies exist and how they behave)",
        "taxonomytranslation": "storefront texts under the translation workflow",
        "term": "navigation structure (taxonomy terms)",
        "termtranslation": "storefront texts under the translation workflow",
        "brand": "manufacturer names shown on product pages and feeds",
        "brandtranslation": "storefront texts under the translation workflow",
        # CAT.3: attribute catalogue (platform data) and what products and variants say of it.
        "attribute": "attribute catalogue (codes, kinds, units); no money, access or legal facts",
        "attributetranslation": "storefront texts under the translation workflow",
        "attributevalue": "values of a choice attribute and their order; no money or access",
        "attributevaluetranslation": "storefront texts under the translation workflow",
        "productattribute": "catalog content describing a product; creation with variants is "
        "audited by services.create_product",
        "variantoption": "which value a variant takes on an axis; created and audited by "
        "services.create_product",
    }

    def ready(self) -> None:
        from . import signals  # noqa: F401  (connects the CAT.14 upsell guard)

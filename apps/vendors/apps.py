from django.apps import AppConfig


class VendorsConfig(AppConfig):
    name = "apps.vendors"
    label = "vendors"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("vendor", "vendormember", "legalfieldrequirement")
    unaudited_models = {
        "vendortranslation": "storefront texts under the translation workflow, no legal or "
        "financial data",
    }

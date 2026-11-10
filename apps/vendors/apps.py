from django.apps import AppConfig


class VendorsConfig(AppConfig):
    name = "apps.vendors"
    label = "vendors"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = (
        "vendor",
        "vendormember",
        "vendorclaim",
        "legalfieldrequirement",
        "vendorcontract",
        "contractacceptance",
        "selfcertification",
        "vendorfeatureflag",
        "crossvendorconsent",
    )
    unaudited_models = {
        "vendortranslation": "storefront texts under the translation workflow, no legal or "
        "financial data",
        "vendorcontracttranslation": "contract texts: drafts until their version is accepted, "
        "then a database trigger refuses any insert, update or delete of them (migration 0011); "
        "each acceptance records the language and the hash of the text accepted",
    }

    def ready(self) -> None:
        from . import services

        services.register_claim_purpose()  # VEN.24: the email-bound claim link (CORE.15)

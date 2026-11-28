from django.apps import AppConfig


class VendorsConfig(AppConfig):
    name = "apps.vendors"
    label = "vendors"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = (
        "vendor",
        "vendormember",
        "vendorclaim",
        "vendorapplication",
        "legalfieldrequirement",
        "vendorcontract",
        "contractacceptance",
        "selfcertification",
        "vendorfeatureflag",
        "crossvendorconsent",
        "vendorplan",
        "commissionrule",
        "salestermsversion",
        "salestermsinforce",
        "returnpolicyversion",
        "notincountry",
        "vendorclosure",
    )
    unaudited_models = {
        "vendortranslation": "storefront texts under the translation workflow, no legal or "
        "financial data",
        "vendorcontracttranslation": "contract texts: drafts until their version is accepted, "
        "then a database trigger refuses any insert, update or delete of them (migration 0011); "
        "each acceptance records the language and the hash of the text accepted",
    }

    def ready(self) -> None:
        from django.core import checks

        from apps.core.public_cache import purge_on_change

        from . import services
        from .checks import check_self_certification_texts
        from .models import Vendor, VendorFeatureFlag

        # SEO.6 2b: save_vendor, delete_vendor, set_business_only and the vendor flags all end in
        # a row save or delete; the storefront and every product page of the vendor change.
        purge_on_change(Vendor, "vendor")
        purge_on_change(VendorFeatureFlag, "vendor flag")

        checks.register(check_self_certification_texts)  # CMP.25 texts, I18N.19 review gate

        services.register_claim_purpose()  # VEN.24: the email-bound claim link (CORE.15)
        services.register_application_purpose()  # VEN.22: the application email link

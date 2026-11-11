from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    name = "apps.compliance"
    label = "compliance"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). GPSR data is a legal fact shown to
    # consumers and authorities: written only through the audited services (CMP.3).
    audited_models = (
        "gpsrinfo",
        "vendorgpsrdefaults",
        "gpsrsafetytext",
        "safetycase",
        "countrywarninglanguages",
        "notice",
    )

    def ready(self) -> None:
        from apps.catalog.publishing import register_publication_check
        from apps.catalog.selling import register_sale_check
        from apps.cms.services import register_golive_check

        from .services import (
            SALE_CHECK_NAME,
            golive_messages,
            gpsr_blocking_messages,
            warnings_sale_messages,
        )

        # CMP.5 (D-27): the DSA and P2B checks join the one go-live gate.
        register_golive_check(golive_messages)

        # CMP.3: a product without complete GPSR data cannot be published (CAT.10 asks).
        register_publication_check("gpsr", gpsr_blocking_messages)
        # CMP.3 (owner decision 2026-11-09): no sale into a country without warnings in one of
        # its languages; the checkout (ORD.3) asks.
        register_sale_check(SALE_CHECK_NAME, warnings_sale_messages)

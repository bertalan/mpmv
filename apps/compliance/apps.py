from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    name = "apps.compliance"
    label = "compliance"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). GPSR data is a legal fact shown to
    # consumers and authorities: written only through the audited services (CMP.3).
    audited_models = ("gpsrinfo", "vendorgpsrdefaults")

    def ready(self) -> None:
        from apps.catalog.publishing import register_publication_check

        from .services import gpsr_blocking_messages

        # CMP.3: a product without complete GPSR data cannot be published (CAT.10 asks).
        register_publication_check("gpsr", gpsr_blocking_messages)

from django.apps import AppConfig


class TaxConfig(AppConfig):
    name = "apps.tax"
    label = "tax"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Rates and profiles are versioned through
    # audited services; territories and legal notes are saved through audited services too.
    audited_models = ("taxrate", "territory", "vendortaxprofile", "vendortaxprofiletranslation")
    unaudited_models = {
        "vatvalidation": "append-only record of VIES answers only (a cache of an external "
        "source, no staff-recorded rows); the rows are the history, view-only admin, and vendor "
        "VAT checks are audited by vendors",
    }

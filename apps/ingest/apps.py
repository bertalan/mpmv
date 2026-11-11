from django.apps import AppConfig


class IngestConfig(AppConfig):
    name = "apps.ingest"
    label = "ingest"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Sources, mappings, runs and review
    # decisions change what the catalogue imports: written through the audited services.
    audited_models = ("suppliersource", "importrun", "reviewitem", "mappingtable")
    unaudited_models = {
        "sourceitem": "staging row rebuilt from the supplier feed on every run",
        "sourceobservation": "append-only copy of what the supplier published",
        "productdraft": "pipeline output awaiting review; publishing it is audited in catalog",
    }

from django.apps import AppConfig


class IngestConfig(AppConfig):
    name = "apps.ingest"
    label = "ingest"

    def ready(self) -> None:
        from django.core import checks

        from .checks import check_enricher_settings

        checks.register(check_enricher_settings)

    # Audit policy (SEC.4, docs/dev/audit-trail.md). Sources, mappings, runs and review
    # decisions change what the catalogue imports: written through the audited services.
    audited_models = (
        "suppliersource",
        "importrun",
        "reviewitem",
        "mappingtable",
        "enrichmentversion",
    )
    unaudited_models = {
        "sourceitem": "staging row rebuilt from the supplier feed on every run",
        "sourceobservation": "append-only copy of what the supplier published",
        "sourcestate": "high-volume staging copy of the supplier's current state, per read",
        "sourcestateversion": "append-only history of the staging state",
        "sourcevariation": "staging row of a supplier variation, rewritten on every read",
        "sourcemissing": "evidence that a supplier page answered 404, cleared on the next read",
        "itemchange": "append-only history of supplier field changes, itself the record",
        "productdraft": "pipeline output awaiting review; publishing it is audited in catalog",
    }

from django.apps import AppConfig


class TrustConfig(AppConfig):
    name = "apps.trust"
    label = "trust"

    # Audit policy (SEC.4, docs/dev/audit-trail.md). A rules change is audited by
    # `services.add_trust_rules`; snapshots are computed results and their gain and loss audit
    # entries come with the refresh task (TRU.2).
    audited_models = ("trustrules",)
    unaudited_models = {
        "vendortrustsnapshot": "append-only computed result (database trigger); the rules "
        "version is kept in every row, and the audit entries for a badge gained or lost are "
        "written by the refresh task (TRU.2)",
    }

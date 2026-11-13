from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    label = "ledger"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("ledgerentry",)

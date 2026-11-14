from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    label = "ledger"

    # Audit policy (SEC.4, docs/dev/audit-trail.md).
    audited_models = ("ledgerentry",)

    def ready(self) -> None:
        from apps.payments.models import Payment
        from apps.payments.signals import on_order_paid

        from .receivers import post_and_queue_transfers

        on_order_paid.connect(
            post_and_queue_transfers, sender=Payment, dispatch_uid="ledger.post_paid_order"
        )

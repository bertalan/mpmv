from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "apps.orders"
    label = "orders"

    # Audit policy (SEC.4, docs/dev/audit-trail.md): orders are written only through
    # `apps.orders.services`, which audits every change (without addresses or e-mail).
    audited_models = ("order", "vendororder", "orderline")
    unaudited_models = {
        "ordernumbersequence": "a counter row locked while a number is taken; the number is "
        "audited with the order it belongs to",
        "stockreservation": "a short-lived technical hold on stock; the order that confirms it "
        "is audited, and the stock change is audited when the payment succeeds",
    }

    def ready(self) -> None:
        # The confirmation PDF is built when the email is sent (ORD.9 3/3); `core` only knows
        # the kind. Importing `documents` does not load WeasyPrint (it is imported on first use).
        from apps.core.services import register_email_attachment

        from . import documents

        register_email_attachment(
            documents.ATTACHMENT_KIND, documents.vendor_confirmation_attachment
        )

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
    }

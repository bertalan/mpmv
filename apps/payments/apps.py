from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "apps.payments"
    label = "payments"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers the PAYMENT_PROVIDER check)

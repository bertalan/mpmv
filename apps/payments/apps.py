from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "apps.payments"
    label = "payments"
    audited_models = ("payment",)
    unaudited_models = {
        "processedevent": "Idempotency record of a webhook event id: no business data; the "
        "payment it confirms is audited (payments.payment.succeeded).",
    }

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers the PAYMENT_PROVIDER check)
        from .registry import register_provider
        from .stripe_provider import provider_from_settings

        register_provider(provider_from_settings)  # Stripe, the only provider at launch (D-09)

"""System checks for the payment provider setting (PAY.5), run by `manage.py check`.

- payments.E001: `PAYMENT_PROVIDER` names a provider that is not registered.
- payments.E002: the fake provider is configured where real data lives (`LIVE_DATA`).
- payments.E004: LIVE_DATA while the vendor transfers (PAY.3) are not wired.
- payments.E003: Stripe is configured on LIVE or STAGE without its keys (PAY.1, PAY.2).
"""

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from .registry import registered_codes


@register()
def check_transfers_wired(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    if settings.LIVE_DATA and not getattr(settings, "PAYMENTS_TRANSFERS_WIRED", False):
        return [
            Error(
                "vendor transfers (PAY.3) are not wired; LIVE would keep the whole payment on "
                "the platform, against D-08.",
                hint="PAY.3 sets PAYMENTS_TRANSFERS_WIRED to True. STAGE is not affected.",
                id="payments.E004",
            )
        ]
    return []


@register()
def check_payment_provider(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    code = settings.PAYMENT_PROVIDER
    if not code:
        return []
    if code not in registered_codes():
        return [
            Error(
                f"PAYMENT_PROVIDER {code!r} is not a registered payment provider.",
                hint=f"Registered: {', '.join(registered_codes()) or 'none'}. See "
                "docs/dev/payment-providers.md.",
                id="payments.E001",
            )
        ]
    if code == "fake" and settings.LIVE_DATA:
        return [
            Error(
                "The fake payment provider cannot be used where LIVE_DATA is on.",
                id="payments.E002",
            )
        ]
    if code == "stripe" and (settings.LIVE_DATA or settings.STAGE):
        missing = [
            name
            for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
            if not getattr(settings, name, "")
        ]
        if missing:
            return [
                Error(
                    f"PAYMENT_PROVIDER is stripe but {', '.join(missing)} is empty.",
                    hint="Set MPMV_STRIPE_SECRET_KEY and MPMV_STRIPE_WEBHOOK_SECRET "
                    "(deploy/server/mpmv.env.example).",
                    id="payments.E003",
                )
            ]
    return []

"""The checkout's way into the payment provider (PAY.1): one payment per order.

`apps.orders` cannot import this app (layers), so `settings.ORDER_PAYMENT_STARTER` names
`start_payment` and orders imports it by path. Separate charges and transfers (D-08): one charge on
the platform account, `transfer_group` = the order number; the per-vendor transfers are made after
the payment succeeds (PAY.3), not here.
"""

from decimal import Decimal

from apps.orders.services import PaymentUnavailable

from . import registry, stripe_engine
from .engine import PaymentRequest, PaymentStatus, ProviderError


def start_payment(
    *, order_ref: str, amount: Decimal, currency: str, idempotency_key: str
) -> tuple[str, str] | None:
    """Create the provider payment for an order; returns `(provider_ref, client_secret)`, or None
    while no provider is configured.

    `amount` is the order's gross total, rounded half up to the currency's minor unit. The shares
    are not known here, so the whole amount is the platform's until PAY.3 splits it. Raises
    `PaymentUnavailable` when the provider refuses or fails."""
    provider = registry.configured_provider()
    if provider is None:
        return None
    charge = stripe_engine.round_to_currency(amount, currency)
    try:
        result = provider.create_payment(
            PaymentRequest(
                order_ref=order_ref,
                currency=currency,
                amount=charge,
                shares=(),
                platform_fee=charge,
                idempotency_key=idempotency_key,
            )
        )
    except ProviderError as error:
        raise PaymentUnavailable(str(error)) from error
    if result.status is PaymentStatus.FAILED:
        return None
    return result.provider_ref, result.client_secret

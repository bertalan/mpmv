"""Pure Stripe helpers (PAY.1, PAY.2): minor units, webhook signature, refund reversal split.

No ORM, no network, no Django. The signature scheme is Stripe's documented one (`Stripe-Signature:
t=<unix>,v1=<hex>`, HMAC-SHA256 over `<t>.<raw body>`), checked here so no SDK touches the raw body.
"""

import hashlib
import hmac
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from .engine import InvalidSignature, ProviderError

# Currencies Stripe counts without minor units; every other one is taken as two decimals.
ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF"})
DEFAULT_TOLERANCE_SECONDS = 300


def minor_exponent(currency: str) -> int:
    return 0 if currency.upper() in ZERO_DECIMAL else 2


def to_minor(amount: Decimal, currency: str) -> int:
    """Whole minor units of `amount`. A provider never rounds: a finer amount is refused."""
    scaled = amount * (10 ** minor_exponent(currency))
    if scaled != scaled.to_integral_value():
        raise ProviderError(f"amount {amount} has more decimals than {currency} allows")
    return int(scaled)


def round_to_currency(amount: Decimal, currency: str) -> Decimal:
    """`amount` rounded half up to the currency's minor unit (two decimals, or none for a
    zero-decimal currency). Used for an order total before it is sent: the provider itself never
    rounds."""
    return amount.quantize(Decimal(1).scaleb(-minor_exponent(currency)), rounding=ROUND_HALF_UP)


def from_minor(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-minor_exponent(currency))


def sign_payload(body: bytes, secret: str, timestamp: int) -> str:
    """The `Stripe-Signature` header value for `body` (used by tests and the dev tooling)."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_signature(
    body: bytes,
    header: str,
    secret: str,
    *,
    now: float,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Raise `InvalidSignature` unless `header` signs `body` with `secret` within `tolerance`."""
    if not secret:
        raise InvalidSignature("no webhook secret configured")
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise InvalidSignature("malformed signature header")
    if abs(now - timestamp) > tolerance:
        raise InvalidSignature("timestamp outside the tolerance")
    expected = sign_payload(body, secret, timestamp).rpartition("v1=")[2]
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise InvalidSignature("signature does not match")


def reversal_amounts(
    transfer_minor: Sequence[int], *, refund_minor: int, charge_minor: int
) -> list[int]:
    """Each vendor transfer's share of a refund, in minor units, `ROUND_HALF_UP` per transfer.

    A refund of `refund_minor` out of `charge_minor` takes back the same fraction of every
    transfer (D-08 `reverse_transfer`); the platform fee part stays with the platform, which
    gives it back through PAY.4. A transfer is never reversed for more than it holds.
    """
    if charge_minor <= 0:
        raise ProviderError("charge amount must be positive")
    reversals: list[int] = []
    for held in transfer_minor:
        part = (Decimal(held) * refund_minor / charge_minor).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        reversals.append(min(int(part), held))
    return reversals


PENDING = "pending"  # onboarding not finished: details not submitted yet
RESTRICTED = "restricted"  # details submitted but charges or payouts are not enabled
# The account can receive transfers and pay out (VEN.6, D-19); the storefront reads the same
# value as `vendors.engine.PAYOUT_ACCOUNT_READY` (kept in step by a unit test).
ACTIVE = "active"


def connect_status(*, details_submitted: bool, charges_enabled: bool, payouts_enabled: bool) -> str:
    """Where a connected account stands. Only `active` lets a vendor sell online (D-19)."""
    if charges_enabled and payouts_enabled and details_submitted:
        return ACTIVE
    return RESTRICTED if details_submitted else PENDING

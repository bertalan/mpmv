"""The Stripe client interface (PAY.1) and its HTTP implementation.

`StripePaymentProvider` talks to Stripe only through `StripeClient`. Tests replace the client with
`apps.payments.tests.fake_stripe.FakeStripeClient`; they never call the network and never hold a
real key. `HttpStripeClient` is the one place that speaks HTTP (standard library, no SDK), with
every request carrying an `Idempotency-Key`.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

API_BASE = "https://api.stripe.com/v1"
TIMEOUT_SECONDS = 20


class StripeError(Exception):
    """Stripe refused or failed a call. The message never carries a key."""


@dataclass(frozen=True)
class StripeIntent:
    id: str
    status: str  # Stripe's PaymentIntent status
    amount_minor: int
    currency: str
    charge_id: str = ""  # `latest_charge`, set once the customer paid
    transfer_group: str = ""
    # What Stripe.js needs in the browser; never logged (no repr) or stored.
    client_secret: str = field(default="", repr=False)


@dataclass(frozen=True)
class StripeTransfer:
    id: str
    destination: str
    amount_minor: int
    currency: str
    created: datetime
    payment_ref: str = ""  # the PaymentIntent id, from the transfer metadata
    transfer_group: str = ""


@dataclass(frozen=True)
class StripeRefund:
    id: str
    amount_minor: int
    currency: str
    fee_minor: int = 0  # fee on the refund's balance transaction (PAY.4 provider cost)


@dataclass(frozen=True)
class StripeCharge:
    """A charge as Stripe reports it, for the refund guard of the transfer sweep (PAY.3)."""

    id: str
    amount_minor: int
    currency: str
    amount_refunded_minor: int = 0  # refunds made anywhere, the Stripe dashboard included


@dataclass(frozen=True)
class StripeAccount:
    """A connected account as Stripe reports it (VEN.6). Only the fields onboarding reads."""

    id: str
    details_submitted: bool = False
    charges_enabled: bool = False
    payouts_enabled: bool = False
    requirements_due: tuple[str, ...] = ()  # currently due and past due, sorted
    disabled_reason: str = ""


def account_from_payload(data: Mapping[str, Any]) -> StripeAccount:
    """The `Account` object of an API answer or of an `account.updated` event."""
    requirements = data.get("requirements") or {}
    due = {
        *(str(x) for x in requirements.get("currently_due") or ()),
        *(str(x) for x in requirements.get("past_due") or ()),
    }
    return StripeAccount(
        id=str(data["id"]),
        details_submitted=bool(data.get("details_submitted")),
        charges_enabled=bool(data.get("charges_enabled")),
        payouts_enabled=bool(data.get("payouts_enabled")),
        requirements_due=tuple(sorted(due)),
        disabled_reason=str(requirements.get("disabled_reason") or ""),
    )


class StripeClient(Protocol):
    def create_payment_intent(
        self,
        *,
        amount_minor: int,
        currency: str,
        transfer_group: str,
        methods: Sequence[str],
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeIntent: ...

    def get_payment_intent(self, intent_id: str) -> StripeIntent: ...

    def get_charge(self, charge_id: str) -> StripeCharge: ...

    def create_transfer(
        self,
        *,
        amount_minor: int,
        currency: str,
        destination: str,
        transfer_group: str,
        source_transaction: str,
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeTransfer: ...

    def list_transfers(
        self, *, created_gte: datetime | None = None, transfer_group: str = ""
    ) -> Sequence[StripeTransfer]: ...

    def create_refund(
        self, *, payment_intent: str, amount_minor: int, idempotency_key: str
    ) -> StripeRefund: ...

    def create_transfer_reversal(
        self, *, transfer_id: str, amount_minor: int, idempotency_key: str
    ) -> str: ...

    def create_account(
        self,
        *,
        country: str,
        email: str,
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeAccount: ...

    def get_account(self, account_id: str) -> StripeAccount: ...

    def create_account_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str: ...


Transport = Callable[[urllib.request.Request], bytes]


def _urlopen(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return bytes(response.read())


def _flatten(params: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Stripe's form encoding: `metadata[order_ref]=x`, `payment_method_types[]=card`."""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, Mapping):
            pairs.extend(_flatten({f"{key}[{inner}]": v for inner, v in value.items()}))
        elif isinstance(value, list | tuple):
            pairs.extend((f"{key}[]", str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    return pairs


@dataclass
class HttpStripeClient:
    secret_key: str = field(repr=False)
    api_version: str = ""
    transport: Transport = _urlopen

    def _call(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any],
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not self.secret_key:
            raise StripeError("Stripe is not configured (no secret key)")
        pairs = _flatten(params)
        url = f"{API_BASE}{path}"
        data: bytes | None = None
        if method == "GET":
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        else:
            data = urllib.parse.urlencode(pairs).encode()
        request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
        request.add_header("Authorization", f"Bearer {self.secret_key}")
        if idempotency_key:
            request.add_header("Idempotency-Key", idempotency_key)
        if self.api_version:
            request.add_header("Stripe-Version", self.api_version)
        try:
            payload = json.loads(self.transport(request))
        except urllib.error.HTTPError as error:
            raise StripeError(f"Stripe answered HTTP {error.code} on {path}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise StripeError(f"Stripe call to {path} failed") from None
        if not isinstance(payload, dict):
            raise StripeError(f"Unexpected Stripe answer on {path}")
        return payload

    @staticmethod
    def _intent(data: Mapping[str, Any]) -> StripeIntent:
        return StripeIntent(
            id=str(data["id"]),
            status=str(data["status"]),
            amount_minor=int(data["amount"]),
            currency=str(data["currency"]).upper(),
            charge_id=str(data.get("latest_charge") or ""),
            transfer_group=str(data.get("transfer_group") or ""),
            client_secret=str(data.get("client_secret") or ""),
        )

    @staticmethod
    def _transfer(data: Mapping[str, Any]) -> StripeTransfer:
        return StripeTransfer(
            id=str(data["id"]),
            destination=str(data["destination"]),
            amount_minor=int(data["amount"]),
            currency=str(data["currency"]).upper(),
            created=datetime.fromtimestamp(int(data["created"]), tz=UTC),
            payment_ref=str((data.get("metadata") or {}).get("payment_ref", "")),
            transfer_group=str(data.get("transfer_group") or ""),
        )

    def create_payment_intent(
        self,
        *,
        amount_minor: int,
        currency: str,
        transfer_group: str,
        methods: Sequence[str],
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeIntent:
        data = self._call(
            "POST",
            "/payment_intents",
            {
                "amount": amount_minor,
                "currency": currency.lower(),
                "transfer_group": transfer_group,
                "payment_method_types": list(methods),
                "metadata": dict(metadata),
            },
            idempotency_key,
        )
        return self._intent(data)

    def get_payment_intent(self, intent_id: str) -> StripeIntent:
        return self._intent(self._call("GET", f"/payment_intents/{intent_id}", {}))

    def get_charge(self, charge_id: str) -> StripeCharge:
        data = self._call("GET", f"/charges/{charge_id}", {})
        try:
            return StripeCharge(
                id=str(data["id"]),
                amount_minor=int(data["amount"]),
                currency=str(data["currency"]).upper(),
                amount_refunded_minor=int(data.get("amount_refunded") or 0),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise StripeError(f"Unexpected Stripe answer on /charges/{charge_id}") from error

    def create_transfer(
        self,
        *,
        amount_minor: int,
        currency: str,
        destination: str,
        transfer_group: str,
        source_transaction: str,
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeTransfer:
        data = self._call(
            "POST",
            "/transfers",
            {
                "amount": amount_minor,
                "currency": currency.lower(),
                "destination": destination,
                "transfer_group": transfer_group,
                "source_transaction": source_transaction,
                "metadata": dict(metadata),
            },
            idempotency_key,
        )
        return self._transfer(data)

    def list_transfers(
        self, *, created_gte: datetime | None = None, transfer_group: str = ""
    ) -> Sequence[StripeTransfer]:
        params: dict[str, Any] = {"limit": 100}
        if created_gte is not None:
            params["created"] = {"gte": int(created_gte.timestamp())}
        if transfer_group:
            params["transfer_group"] = transfer_group
        found: list[StripeTransfer] = []
        while True:
            page = self._call("GET", "/transfers", params)
            rows = page.get("data", [])
            found.extend(self._transfer(row) for row in rows)
            if not page.get("has_more") or not rows:
                return found
            params["starting_after"] = rows[-1]["id"]

    def create_refund(
        self, *, payment_intent: str, amount_minor: int, idempotency_key: str
    ) -> StripeRefund:
        data = self._call(
            "POST",
            "/refunds",
            {
                "payment_intent": payment_intent,
                "amount": amount_minor,
                "expand": ["balance_transaction"],
            },
            idempotency_key,
        )
        balance = data.get("balance_transaction")
        fee = int(balance.get("fee", 0)) if isinstance(balance, dict) else 0
        return StripeRefund(
            id=str(data["id"]),
            amount_minor=int(data["amount"]),
            currency=str(data["currency"]).upper(),
            fee_minor=fee,
        )

    def create_transfer_reversal(
        self, *, transfer_id: str, amount_minor: int, idempotency_key: str
    ) -> str:
        data = self._call(
            "POST",
            f"/transfers/{transfer_id}/reversals",
            {"amount": amount_minor},
            idempotency_key,
        )
        return str(data["id"])

    def create_account(
        self,
        *,
        country: str,
        email: str,
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeAccount:
        params: dict[str, Any] = {
            "type": "express",
            "country": country.upper(),
            "capabilities": {
                "card_payments": {"requested": "true"},
                "transfers": {"requested": "true"},
            },
            "metadata": dict(metadata),
        }
        if email:
            params["email"] = email
        return account_from_payload(self._call("POST", "/accounts", params, idempotency_key))

    def get_account(self, account_id: str) -> StripeAccount:
        return account_from_payload(self._call("GET", f"/accounts/{account_id}", {}))

    def create_account_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str:
        data = self._call(
            "POST",
            "/account_links",
            {
                "account": account_id,
                "type": "account_onboarding",
                "refresh_url": refresh_url,
                "return_url": return_url,
            },
        )
        return str(data["url"])

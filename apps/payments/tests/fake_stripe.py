"""In-memory `StripeClient` for tests: the only double of Stripe, behind our own interface.

It behaves like Stripe where the provider relies on it: idempotency keys replay the first answer,
a refund above the charge is refused, a transfer needs a charge. No network, no key.
"""

import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from apps.payments.stripe_client import (
    StripeAccount,
    StripeError,
    StripeIntent,
    StripeRefund,
    StripeTransfer,
)


class FakeStripeClient:
    def __init__(self, *, confirm: bool = True, refund_fee_minor: int = 0) -> None:
        self.confirm = confirm  # True: the customer pays at once (status succeeded)
        self.refund_fee_minor = refund_fee_minor
        self.intents: dict[str, StripeIntent] = {}
        self.transfers: dict[str, StripeTransfer] = {}
        self.reversals: list[tuple[str, int]] = []
        self.transfer_calls: list[dict[str, object]] = []
        self.calls: list[str] = []
        self.accounts: dict[str, StripeAccount] = {}
        self.account_calls: list[dict[str, object]] = []
        self.links: list[str] = []
        self._replies: dict[str, object] = {}
        self._refunded: dict[str, int] = {}

    def _replay(self, key: str) -> object | None:
        return self._replies.get(key)

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
        self.calls.append("create_payment_intent")
        if (cached := self._replay(f"pi:{idempotency_key}")) is not None:
            assert isinstance(cached, StripeIntent)
            return cached
        intent_id = f"pi_{secrets.token_hex(6)}"
        intent = StripeIntent(
            id=intent_id,
            status="succeeded" if self.confirm else "requires_payment_method",
            amount_minor=amount_minor,
            currency=currency.upper(),
            charge_id=f"ch_{secrets.token_hex(6)}" if self.confirm else "",
            transfer_group=transfer_group,
            client_secret=f"{intent_id}_secret_{secrets.token_urlsafe(12)}",
        )
        self.intents[intent_id] = intent
        self._replies[f"pi:{idempotency_key}"] = intent
        return intent

    def get_payment_intent(self, intent_id: str) -> StripeIntent:
        if intent_id not in self.intents:
            raise StripeError("No such payment_intent")
        return self.intents[intent_id]

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
        self.calls.append("create_transfer")
        self.transfer_calls.append(
            {"source_transaction": source_transaction, "destination": destination}
        )
        if (cached := self._replay(f"tr:{idempotency_key}")) is not None:
            assert isinstance(cached, StripeTransfer)
            return cached
        if not any(i.charge_id == source_transaction for i in self.intents.values()):
            raise StripeError("No such charge")
        transfer = StripeTransfer(
            id=f"tr_{secrets.token_hex(6)}",
            destination=destination,
            amount_minor=amount_minor,
            currency=currency.upper(),
            created=datetime.now(UTC),
            payment_ref=metadata.get("payment_ref", ""),
            transfer_group=transfer_group,
        )
        self.transfers[transfer.id] = transfer
        self._replies[f"tr:{idempotency_key}"] = transfer
        return transfer

    def list_transfers(
        self, *, created_gte: datetime | None = None, transfer_group: str = ""
    ) -> Sequence[StripeTransfer]:
        return [
            t
            for t in self.transfers.values()
            if (created_gte is None or t.created >= created_gte)
            and (not transfer_group or t.transfer_group == transfer_group)
        ]

    def create_refund(
        self, *, payment_intent: str, amount_minor: int, idempotency_key: str
    ) -> StripeRefund:
        self.calls.append("create_refund")
        if (cached := self._replay(f"re:{idempotency_key}")) is not None:
            assert isinstance(cached, StripeRefund)
            return cached
        intent = self.get_payment_intent(payment_intent)
        already = self._refunded.get(payment_intent, 0)
        if amount_minor <= 0 or already + amount_minor > intent.amount_minor:
            raise StripeError("Refund amount is greater than the charge")
        self._refunded[payment_intent] = already + amount_minor
        refund = StripeRefund(
            f"re_{secrets.token_hex(6)}", amount_minor, intent.currency, self.refund_fee_minor
        )
        self._replies[f"re:{idempotency_key}"] = refund
        return refund

    def create_transfer_reversal(
        self, *, transfer_id: str, amount_minor: int, idempotency_key: str
    ) -> str:
        self.calls.append("create_transfer_reversal")
        if (cached := self._replay(f"rv:{idempotency_key}")) is not None:
            assert isinstance(cached, str)
            return cached
        if transfer_id not in self.transfers:
            raise StripeError("No such transfer")
        self.reversals.append((transfer_id, amount_minor))
        reversal = f"trr_{secrets.token_hex(6)}"
        self._replies[f"rv:{idempotency_key}"] = reversal
        return reversal

    # -- Connect onboarding (VEN.6)

    def create_account(
        self,
        *,
        country: str,
        email: str,
        metadata: Mapping[str, str],
        idempotency_key: str,
    ) -> StripeAccount:
        self.calls.append("create_account")
        if (cached := self._replay(f"acct:{idempotency_key}")) is not None:
            assert isinstance(cached, StripeAccount)
            return cached
        self.account_calls.append({"country": country, "email": email, "metadata": dict(metadata)})
        account = StripeAccount(id=f"acct_{secrets.token_hex(6)}")
        self.accounts[account.id] = account
        self._replies[f"acct:{idempotency_key}"] = account
        return account

    def get_account(self, account_id: str) -> StripeAccount:
        self.calls.append("get_account")
        if account_id not in self.accounts:
            raise StripeError("No such account")
        return self.accounts[account_id]

    def create_account_link(self, *, account_id: str, refresh_url: str, return_url: str) -> str:
        self.calls.append("create_account_link")
        if account_id not in self.accounts:
            raise StripeError("No such account")
        url = f"https://connect.example.test/setup/{account_id}/{secrets.token_hex(4)}"
        self.links.append(url)
        return url

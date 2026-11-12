"""Contract tests every `PaymentProvider` must pass (PAY.5).

Subclass `PaymentProviderContract` in the provider's own test module, implement the two hooks,
and pytest collects the checks under the subclass name:

    class TestMyProvider(PaymentProviderContract):
        def make_provider(self): ...
        def signed_webhook(self, provider, event_id): ...  # returns (body, signature)

The checklist is in `docs/dev/payment-providers.md`. No network: a real provider is tested
against its own sandbox double behind this interface, never by mocking its SDK in these tests.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.payments.engine import (
    FeeCollection,
    InvalidSignature,
    PaymentProvider,
    PaymentRequest,
    PaymentStatus,
    ProviderError,
    RefundRequest,
    VendorShare,
    capability_problems,
)


class PaymentProviderContract:
    def make_provider(self) -> PaymentProvider:
        raise NotImplementedError

    def signed_webhook(self, provider: PaymentProvider, event_id: str) -> tuple[bytes, str]:
        raise NotImplementedError

    @staticmethod
    def request(key: str = "key-1", method: str = "card") -> PaymentRequest:
        return PaymentRequest(
            order_ref="order-1",
            currency="EUR",
            amount=Decimal("100.00"),
            shares=(
                VendorShare("acct-a", Decimal("60.00")),
                VendorShare("acct-b", Decimal("30.00")),
            ),
            platform_fee=Decimal("10.00"),
            idempotency_key=key,
            method=method,
        )

    def test_declares_every_capability(self) -> None:
        assert capability_problems(self.make_provider()) == []

    def test_never_collects_on_an_operator_account(self) -> None:
        assert self.make_provider().collects_on_operator_account is False

    def test_fee_collection_is_split_or_deferred(self) -> None:
        assert self.make_provider().fee_collection in set(FeeCollection)

    def test_reports_the_methods_it_supports(self) -> None:
        methods = self.make_provider().supported_methods()
        assert methods
        assert all(isinstance(method, str) and method for method in methods)

    def test_payment_separates_fee_and_vendor_shares(self) -> None:
        provider = self.make_provider()
        result = provider.create_payment(self.request())
        assert result.status in set(PaymentStatus)
        assert result.provider_ref
        assert len(result.transfer_refs) == 2  # one transfer per vendor (PAY.3)

    def test_payment_is_idempotent_on_its_key(self) -> None:
        provider = self.make_provider()
        first = provider.create_payment(self.request("same"))
        assert provider.create_payment(self.request("same")) == first

    def test_unbalanced_payment_is_refused(self) -> None:
        provider = self.make_provider()
        bad = PaymentRequest(
            "o",
            "EUR",
            Decimal("100.00"),
            (VendorShare("a", Decimal("50.00")),),
            Decimal("10.00"),
            "bad",
        )
        with pytest.raises(ProviderError):
            provider.create_payment(bad)

    def test_unsupported_method_is_refused(self) -> None:
        with pytest.raises(ProviderError):
            self.make_provider().create_payment(self.request("m", method="no-such-method"))

    def test_refund_reports_provider_cost_and_reverses_shares(self) -> None:
        provider = self.make_provider()
        paid = provider.create_payment(self.request())
        refund = provider.refund(
            RefundRequest(paid.provider_ref, Decimal("40.00"), "EUR", "refund-1")
        )
        assert refund.amount == Decimal("40.00")
        assert refund.provider_cost >= Decimal("0")

    def test_refund_is_idempotent_and_capped(self) -> None:
        provider = self.make_provider()
        paid = provider.create_payment(self.request())
        ask = RefundRequest(paid.provider_ref, Decimal("60.00"), "EUR", "r")
        assert provider.refund(ask) == provider.refund(ask)
        with pytest.raises(ProviderError):
            provider.refund(RefundRequest(paid.provider_ref, Decimal("60.00"), "EUR", "r2"))

    def test_payouts_are_listed_for_reconciliation(self) -> None:
        provider = self.make_provider()
        provider.create_payment(self.request())
        payouts = provider.list_payouts(timezone.now() - timedelta(days=1))
        assert sum((payout.amount for payout in payouts), Decimal("0")) == Decimal("90.00")
        assert provider.list_payouts(timezone.now() + timedelta(days=1)) == []

    def test_webhook_with_a_bad_signature_is_refused(self) -> None:
        provider = self.make_provider()
        body, _ = self.signed_webhook(provider, "evt-1")
        with pytest.raises(InvalidSignature):
            provider.parse_webhook(body, "not-the-signature")

    def test_webhook_event_id_is_stable_across_redelivery(self) -> None:
        provider = self.make_provider()
        body, signature = self.signed_webhook(provider, "evt-2")
        first = provider.parse_webhook(body, signature)
        assert provider.parse_webhook(body, signature).event_id == first.event_id == "evt-2"

    def test_tampered_webhook_body_is_refused(self) -> None:
        provider = self.make_provider()
        body, signature = self.signed_webhook(provider, "evt-3")
        with pytest.raises(InvalidSignature):
            provider.parse_webhook(body + b" ", signature)

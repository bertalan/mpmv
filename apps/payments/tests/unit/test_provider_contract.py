"""The fake provider passes the contract; the registry and the check refuse what they must."""

import json
from decimal import Decimal

import pytest
from django.core.checks import run_checks
from django.test import override_settings

from apps.payments import registry
from apps.payments.engine import (
    FeeCollection,
    PaymentProvider,
    PaymentRequest,
    VendorShare,
    capability_problems,
    validate_payment_request,
)
from apps.payments.fake import FakePaymentProvider
from apps.payments.tests.provider_contract import PaymentProviderContract


class TestFakeProviderContract(PaymentProviderContract):
    def make_provider(self) -> PaymentProvider:
        return FakePaymentProvider()

    def signed_webhook(self, provider: PaymentProvider, event_id: str) -> tuple[bytes, str]:
        assert isinstance(provider, FakePaymentProvider)
        body = json.dumps({"id": event_id, "type": "payment.succeeded"}).encode()
        return body, provider.sign(body)


def test_fake_is_a_payment_provider() -> None:
    assert isinstance(FakePaymentProvider(), PaymentProvider)


class NoRefunds(FakePaymentProvider):
    code = "norefunds"
    refund = None  # type: ignore[assignment]


class OperatorAccount(FakePaymentProvider):
    code = "operatorbank"
    collects_on_operator_account = True


class NoFeeModel(FakePaymentProvider):
    code = "nofee"
    fee_collection = None  # type: ignore[assignment]


@pytest.mark.parametrize("provider_class", [NoRefunds, OperatorAccount, NoFeeModel])
def test_provider_missing_a_capability_is_refused_at_registration(
    provider_class: type[FakePaymentProvider],
) -> None:
    assert capability_problems(provider_class())
    with pytest.raises(registry.ProviderRefused):
        registry.register_provider(provider_class)
    assert provider_class.code not in registry.registered_codes()


def test_registered_provider_is_selected_by_the_setting() -> None:
    class Other(FakePaymentProvider):
        code = "other"
        fee_collection = FeeCollection.DEFERRED_RECEIVABLE

    registry.register_provider(Other)
    try:
        with override_settings(PAYMENT_PROVIDER="other"):
            provider = registry.configured_provider()
            assert provider is not None
            assert provider.code == "other"
        with pytest.raises(registry.ProviderRefused):
            registry.register_provider(Other)  # duplicate code
    finally:
        registry.unregister_provider("other")


def test_no_provider_configured_is_allowed_until_pay1() -> None:
    with override_settings(PAYMENT_PROVIDER=""):
        assert registry.configured_provider() is None
        assert [m for m in run_checks() if (m.id or "").startswith("payments.")] == []


def test_unknown_provider_fails_the_system_check() -> None:
    with override_settings(PAYMENT_PROVIDER="nope"):
        assert [m.id for m in run_checks()] == ["payments.E001"]
        with pytest.raises(registry.UnknownProvider):
            registry.configured_provider()


def test_fake_provider_is_refused_where_live_data_is_on() -> None:
    with override_settings(PAYMENT_PROVIDER="fake", LIVE_DATA=True):
        assert "payments.E002" in [m.id for m in run_checks()]
    with override_settings(PAYMENT_PROVIDER="fake"):
        assert [m.id for m in run_checks() if (m.id or "").startswith("payments.")] == []


def test_fake_provider_is_not_registered_where_settings_forbid_it() -> None:
    with override_settings(PAYMENT_ALLOW_FAKE_PROVIDER=False, PAYMENT_PROVIDER="fake"):
        assert "fake" not in registry.registered_codes()
        assert [m.id for m in run_checks()] == ["payments.E001"]


def test_request_validation_checks_the_amounts() -> None:
    good = PaymentRequest(
        "o", "EUR", Decimal("10"), (VendorShare("a", Decimal("9")),), Decimal("1"), "k"
    )
    assert validate_payment_request(good) == []
    bad = PaymentRequest(
        "o", "EUR", Decimal("10"), (VendorShare("a", Decimal("9")),), Decimal("2"), ""
    )
    assert len(validate_payment_request(bad)) == 2

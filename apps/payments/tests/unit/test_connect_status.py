"""VEN.6: the connected account status and the account payload parsing (pure)."""

import pytest

from apps.payments import stripe_engine
from apps.payments.stripe_client import account_from_payload
from apps.vendors import engine as vendors_engine


@pytest.mark.parametrize(
    ("submitted", "charges", "payouts", "expected"),
    [
        (False, False, False, "pending"),
        (True, False, False, "restricted"),
        (True, True, False, "restricted"),
        (True, False, True, "restricted"),
        (True, True, True, "active"),
        (False, True, True, "pending"),
    ],
)
def test_status_is_active_only_when_everything_is_enabled(
    submitted: bool, charges: bool, payouts: bool, expected: str
) -> None:
    got = stripe_engine.connect_status(
        details_submitted=submitted, charges_enabled=charges, payouts_enabled=payouts
    )
    assert got == expected


def test_the_payload_collects_due_requirements_and_the_disabled_reason() -> None:
    account = account_from_payload(
        {
            "id": "acct_1",
            "details_submitted": True,
            "charges_enabled": False,
            "requirements": {
                "currently_due": ["external_account", "individual.dob.day"],
                "past_due": ["external_account"],
                "disabled_reason": "requirements.past_due",
            },
        }
    )
    assert account.requirements_due == ("external_account", "individual.dob.day")
    assert account.disabled_reason == "requirements.past_due"
    assert account.payouts_enabled is False


def test_the_storefront_reads_the_same_ready_status() -> None:
    """The storefront (vendors.services.sells_online) cannot import payments, so it names the
    ready status itself; this keeps both in step (D-19)."""
    assert vendors_engine.PAYOUT_ACCOUNT_READY == stripe_engine.ACTIVE

"""VEN.6: the connected account status and the account payload parsing (pure)."""

import pytest

from apps.payments import stripe_engine
from apps.payments.stripe_client import account_from_payload


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

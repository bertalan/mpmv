"""VEN.5: the onboarding rules (pure): who may accept the contract, the onboarding checklist and
the activation gate.

Only the acceptance of the current contract blocks activation. The DAC7 dataset and the payout
data are listed but never block it: completeness gates payouts (VEN.12 `assert_payout_allowed`),
never a status change.
"""

import datetime as dt

import pytest

from apps.vendors import engine
from apps.vendors.engine import (
    ACTIVE,
    APPLICANT,
    CLOSED,
    ONBOARDING,
    OWNER,
    STAFF,
    SUSPENDED,
    ActivationNotAllowed,
    Completeness,
    ContractNotAcceptable,
    InvalidTransition,
    check_activation,
    check_can_accept_contract,
    contract_start_error,
    onboarding_checklist,
)

COMPLETE = Completeness()


def checklist(
    *, accepted: bool = True, dac7: tuple[str, ...] = (), payout: Completeness = COMPLETE
) -> engine.OnboardingChecklist:
    return onboarding_checklist(
        contract_version=3 if accepted else None,
        current_contract_version=3,
        dac7_missing=dac7,
        payout=payout,
    )


def test_the_checklist_lists_contract_dac7_and_payout_data_in_order() -> None:
    items = checklist().items
    assert [item.key for item in items] == [engine.CONTRACT, engine.DAC7, engine.PAYOUT_DATA]
    assert [item.blocking for item in items] == [True, False, False]


def test_a_vendor_that_accepted_the_current_contract_is_ready_even_with_data_missing() -> None:
    result = checklist(
        dac7=("iban", "tax_residence_country"),
        payout=Completeness(missing=("iban",), invalid=("pec",)),
    )
    assert result.ready
    assert result.blocking_missing == ()
    by_key = {item.key: item for item in result.items}
    assert not by_key[engine.DAC7].done
    assert by_key[engine.DAC7].missing == ("iban", "tax_residence_country")
    assert not by_key[engine.PAYOUT_DATA].done
    assert by_key[engine.PAYOUT_DATA].missing == ("iban", "pec")


def test_without_the_current_contract_the_vendor_is_not_ready() -> None:
    result = checklist(accepted=False)
    assert not result.ready
    assert result.blocking_missing == (engine.CONTRACT,)


def test_accepting_an_older_version_does_not_count() -> None:
    result = onboarding_checklist(
        contract_version=2, current_contract_version=3, dac7_missing=(), payout=COMPLETE
    )
    assert not result.ready
    contract = result.items[0]
    assert contract.missing == ("version 3",)
    assert result.current_contract_version == 3  # what the admin shows


def test_without_any_contract_nobody_can_be_activated() -> None:
    result = onboarding_checklist(
        contract_version=None, current_contract_version=None, dac7_missing=(), payout=COMPLETE
    )
    assert not result.ready
    assert result.items[0].missing == ("no contract is in force",)
    assert result.current_contract_version is None


def test_activation_passes_when_ready() -> None:
    check_activation(checklist())


def test_activation_is_refused_with_what_blocks_it() -> None:
    with pytest.raises(ActivationNotAllowed, match="contract") as exc_info:
        check_activation(checklist(accepted=False))
    assert exc_info.value.blocking == (engine.CONTRACT,)


def test_a_refused_activation_is_an_invalid_transition() -> None:
    """Every adapter that handles `InvalidTransition` (admin actions) reports it as such."""
    assert issubclass(ActivationNotAllowed, InvalidTransition)


@pytest.mark.parametrize("status", [ONBOARDING, ACTIVE, SUSPENDED])
def test_owners_of_an_approved_vendor_may_accept_the_contract(status: str) -> None:
    check_can_accept_contract(status, OWNER)


@pytest.mark.parametrize("status", [APPLICANT, CLOSED])
def test_applicants_and_closed_vendors_cannot_accept_it(status: str) -> None:
    with pytest.raises(ContractNotAcceptable, match=status):
        check_can_accept_contract(status, OWNER)


@pytest.mark.parametrize("role", [STAFF, None])
def test_only_an_owner_accepts_on_behalf_of_the_vendor(role: str | None) -> None:
    with pytest.raises(ContractNotAcceptable, match="owner"):
        check_can_accept_contract(ONBOARDING, role)


NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.UTC)


def test_the_first_contract_version_can_start_any_time() -> None:
    assert contract_start_error(NOW, None) is None


def test_a_new_version_starts_after_the_latest_one() -> None:
    assert contract_start_error(NOW, NOW - dt.timedelta(days=1)) is None
    assert contract_start_error(NOW, NOW) is not None
    assert contract_start_error(NOW, NOW + dt.timedelta(days=1)) is not None

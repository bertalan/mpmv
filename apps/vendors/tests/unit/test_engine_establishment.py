"""CMP.13 (D-21): where a vendor is established, and the activation gate on the allowed set.

Pure rules on the VEN.12 identity: a vendor is admissible when its registered office or its
permanent establishment is in a country where the platform admits vendors.
"""

import pytest

from apps.core.countries import EU_MEMBER_STATES
from apps.vendors import engine

EU = frozenset(EU_MEMBER_STATES)


def identity(
    country: str = "IT", registered_country: str = "", establishment_country: str = ""
) -> engine.LegalIdentity:
    return engine.LegalIdentity(
        country=country,
        registered_country=registered_country,
        establishment_country=establishment_country,
    )


def test_the_establishment_is_the_registered_office_and_the_permanent_establishment() -> None:
    place = engine.vendor_establishment(
        identity(country="CN", registered_country="CN", establishment_country="DE")
    )
    assert place == engine.Establishment(registered_office="CN", permanent_establishment="DE")
    assert place.countries == ("CN", "DE")


def test_without_a_registered_office_country_the_vendor_country_is_the_registered_office() -> None:
    place = engine.vendor_establishment(identity(country="RS"))
    assert place == engine.Establishment(registered_office="RS", permanent_establishment="")
    assert place.countries == ("RS",)


@pytest.mark.parametrize(
    ("registered", "establishment"),
    [("IT", ""), ("DE", ""), ("CN", "IT"), ("RS", "AT"), ("IT", "CH")],
)
def test_a_vendor_established_in_an_allowed_country_is_admissible(
    registered: str, establishment: str
) -> None:
    place = engine.Establishment(registered, establishment)
    assert engine.establishment_error(place, EU) is None
    engine.check_establishment(place, EU)  # no error


@pytest.mark.parametrize("country", ["RS", "CN", "CH", "GB", "US"])
def test_a_vendor_registered_outside_the_set_without_an_establishment_in_it_is_refused(
    country: str,
) -> None:
    place = engine.Establishment(registered_office=country)
    error = engine.establishment_error(place, EU)
    assert error is not None
    assert country in error
    assert "permanent establishment" in error
    with pytest.raises(engine.EstablishmentNotAllowed, match=country) as exc:
        engine.check_establishment(place, EU)
    assert exc.value.establishment == place


def test_a_permanent_establishment_outside_the_set_does_not_help() -> None:
    place = engine.Establishment(registered_office="CN", permanent_establishment="CH")
    error = engine.establishment_error(place, EU)
    assert error is not None
    assert "CN" in error
    assert "CH" in error


def test_the_gate_follows_the_allowed_set_it_is_given() -> None:
    place = engine.Establishment(registered_office="DE")
    assert engine.establishment_error(place, frozenset({"IT"})) is not None
    assert engine.establishment_error(place, frozenset({"IT", "DE"})) is None


def test_a_refused_establishment_is_an_invalid_status_change() -> None:
    """The admin and other adapters already report `InvalidTransition` to staff."""
    assert issubclass(engine.EstablishmentNotAllowed, engine.InvalidTransition)

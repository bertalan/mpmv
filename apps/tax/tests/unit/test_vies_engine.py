"""TAX.5: pure rules of the VIES check (spec 05 §5.2, "VIES fails closed").

What can be asked to VIES, how a VIES REST answer is read, and why a number counts as not
verified. Anything VIES does not clearly confirm is not verified: no reverse charge.
"""

from typing import Any

import pytest

from apps.tax import engine
from apps.tax.engine import NotCheckable, ViesAnswer, ViesQuery

# --- What is asked


@pytest.mark.parametrize(
    ("country", "raw", "expected"),
    [
        ("DE", "123456789", ViesQuery("DE", "DE", "123456789")),
        ("DE", "de 123 456 789", ViesQuery("DE", "DE", "123456789")),
        ("de", "DE123456789", ViesQuery("DE", "DE", "123456789")),
        ("AT", "ATU12345678", ViesQuery("AT", "AT", "U12345678")),
        ("BE", "BE 0123.456.789", ViesQuery("BE", "BE", "0123456789")),
        ("GR", "EL123456789", ViesQuery("GR", "EL", "123456789")),
        ("GR", "GR123456789", ViesQuery("GR", "EL", "123456789")),
        ("EL", "123456789", ViesQuery("GR", "EL", "123456789")),
        ("NL", "NL-123456789-B01", ViesQuery("NL", "NL", "123456789B01")),
        ("ES", "ESB12345678", ViesQuery("ES", "ES", "B12345678")),
        # A French number's two-character key may be letters: they belong to the number.
        ("FR", "AB123456789", ViesQuery("FR", "FR", "AB123456789")),
        ("FR", "FRAB123456789", ViesQuery("FR", "FR", "AB123456789")),
    ],
)
def test_the_vies_query_strips_the_country_prefix_and_separators(
    country: str, raw: str, expected: ViesQuery
) -> None:
    assert engine.vies_query(country, raw) == expected


def test_the_full_number_carries_the_vies_prefix() -> None:
    assert ViesQuery("GR", "EL", "123456789").full_number == "EL123456789"


@pytest.mark.parametrize(
    ("country", "raw"),
    [
        ("DE", "ATU12345678"),  # another member state's number
        ("DE", "INVALID12345"),  # spec 05 T2: a prefix that is not the country's
        ("IT", "FR12345678901"),
    ],
)
def test_a_prefix_different_from_the_country_is_not_checked(country: str, raw: str) -> None:
    with pytest.raises(NotCheckable) as caught:
        engine.vies_query(country, raw)
    assert caught.value.reason == engine.VAT_REASON_PREFIX_MISMATCH
    assert country in caught.value.message


@pytest.mark.parametrize("country", ["GB", "CH", "US", "NO", "XX", ""])
def test_a_country_outside_vies_is_not_checked(country: str) -> None:
    with pytest.raises(NotCheckable) as caught:
        engine.vies_query(country, "123456789")
    assert caught.value.reason == engine.VAT_REASON_NOT_IN_VIES


@pytest.mark.parametrize("raw", ["", "  ", "DE", "1", "1234567890123", "12345#789", "DE 12_345"])
def test_a_malformed_number_is_not_checked(raw: str) -> None:
    with pytest.raises(NotCheckable) as caught:
        engine.vies_query("DE", raw)
    assert caught.value.reason == engine.VAT_REASON_MALFORMED


def test_every_eu_member_state_is_checked_in_vies() -> None:
    assert {"AT", "DE", "FR", "GR", "IT"} <= engine.VIES_COUNTRIES
    assert "GB" not in engine.VIES_COUNTRIES


# --- How a VIES REST answer is read

DE = ViesQuery("DE", "DE", "123456789")


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "countryCode": "DE",
        "vatNumber": "123456789",
        "requestDate": "2026-11-07T10:00:00.000Z",
        "valid": True,
        "requestIdentifier": "WAPIAAAAZ1x2y3",
        "name": "Example Customer GmbH",
        "address": "Example Strasse 1\n10115 Berlin",
        "userError": "VALID",
    }
    payload.update(overrides)
    return payload


def test_a_valid_answer_keeps_name_address_and_consultation_number() -> None:
    assert engine.read_vies_answer(DE, _payload()) == ViesAnswer(
        result=engine.VAT_VALID,
        name="Example Customer GmbH",
        address="Example Strasse 1\n10115 Berlin",
        request_id="WAPIAAAAZ1x2y3",
        request_date="2026-11-07T10:00:00.000Z",
    )


def test_an_invalid_answer_is_invalid() -> None:
    answer = engine.read_vies_answer(
        DE, _payload(valid=False, userError="INVALID", name="---", address="---")
    )
    assert answer == ViesAnswer(
        result=engine.VAT_INVALID,
        request_id="WAPIAAAAZ1x2y3",
        request_date="2026-11-07T10:00:00.000Z",
    )


def test_the_placeholder_for_undisclosed_name_and_address_is_dropped() -> None:
    answer = engine.read_vies_answer(DE, _payload(name="---", address="---"))
    assert (answer.result, answer.name, answer.address) == (engine.VAT_VALID, "", "")


def test_input_vies_refuses_counts_as_invalid() -> None:
    answer = engine.read_vies_answer(DE, _payload(valid=False, userError="INVALID_INPUT"))
    assert (answer.result, answer.detail) == (engine.VAT_INVALID, "INVALID_INPUT")


@pytest.mark.parametrize(
    "error",
    [
        "MS_UNAVAILABLE",
        "SERVICE_UNAVAILABLE",
        "TIMEOUT",
        "MS_MAX_CONCURRENT_REQ",
        "GLOBAL_MAX_CONCURRENT_REQ",
        "IP_BLOCKED",
        "SOMETHING_NEW",
    ],
)
def test_a_vies_error_is_unavailable_with_its_code(error: str) -> None:
    answer = engine.read_vies_answer(DE, _payload(valid=False, userError=error))
    assert (answer.result, answer.detail) == (engine.VAT_UNAVAILABLE, error)


def test_a_valid_flag_next_to_an_error_code_is_not_trusted() -> None:
    answer = engine.read_vies_answer(DE, _payload(valid=True, userError="MS_UNAVAILABLE"))
    assert answer.result == engine.VAT_UNAVAILABLE


def test_the_error_wrapper_of_a_failed_request_is_unavailable() -> None:
    payload = {"actionSucceed": False, "errorWrappers": [{"error": "MS_UNAVAILABLE"}]}
    answer = engine.read_vies_answer(DE, payload)
    assert (answer.result, answer.detail) == (engine.VAT_UNAVAILABLE, "MS_UNAVAILABLE")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"valid": "true", "userError": "VALID"},
        {"valid": None},
        {"errorWrappers": "broken"},
        _payload(countryCode="AT"),
        _payload(vatNumber="999999999"),
    ],
)
def test_an_answer_that_is_not_clearly_about_the_number_is_unavailable(
    payload: dict[str, Any],
) -> None:
    answer = engine.read_vies_answer(DE, payload)
    assert (answer.result, answer.detail) == (engine.VAT_UNAVAILABLE, engine.DETAIL_BAD_RESPONSE)


# --- Why a number counts as verified or not


@pytest.mark.parametrize(
    ("result", "detail", "reason"),
    [
        (engine.VAT_VALID, "", engine.VAT_REASON_VALID),
        (engine.VAT_INVALID, "", engine.VAT_REASON_INVALID),
        (engine.VAT_INVALID, "INVALID_INPUT", engine.VAT_REASON_INVALID),
        (engine.VAT_UNAVAILABLE, engine.DETAIL_TIMEOUT, engine.VAT_REASON_TIMEOUT),
        (engine.VAT_UNAVAILABLE, "TIMEOUT", engine.VAT_REASON_TIMEOUT),
        (engine.VAT_UNAVAILABLE, "MS_UNAVAILABLE", engine.VAT_REASON_UNAVAILABLE),
        (engine.VAT_UNAVAILABLE, "", engine.VAT_REASON_UNAVAILABLE),
    ],
)
def test_each_answer_has_a_reason(result: str, detail: str, reason: str) -> None:
    assert engine.vat_check_reason(result, detail) == reason


def test_only_a_valid_answer_is_verified() -> None:
    assert engine.is_verified(engine.VAT_VALID) is True
    for result in (engine.VAT_INVALID, engine.VAT_UNAVAILABLE, None, "", "VALID"):
        assert engine.is_verified(result) is False


@pytest.mark.parametrize(
    "reason",
    [
        engine.VAT_REASON_INVALID,
        engine.VAT_REASON_UNAVAILABLE,
        engine.VAT_REASON_TIMEOUT,
        engine.VAT_REASON_MALFORMED,
        engine.VAT_REASON_PREFIX_MISMATCH,
        engine.VAT_REASON_NOT_IN_VIES,
    ],
)
def test_every_refusal_says_that_vat_is_charged(reason: str) -> None:
    message = engine.vat_check_message(reason, country="DE")
    assert "not verified" in message
    assert "VAT is charged" in message


def test_the_valid_message_says_vies_confirmed_it() -> None:
    assert "VIES confirmed" in engine.vat_check_message(engine.VAT_REASON_VALID, country="DE")
